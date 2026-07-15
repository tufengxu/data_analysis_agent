"""PythonAnalysisTool: execute Python code in a restricted subprocess.

Pre-installed: pandas, numpy, matplotlib, seaborn, plotly
Resource limits: configurable timeout (default 30s)
"""

from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import json
import logging
import os
import resource
import shutil
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..kernel.manager import (
    KernelCrashError,
    KernelError,
    KernelManager,
    KernelStartError,
    KernelTimeoutError,
)
from ..sampling import render
from ..sampling.config import SamplingConfig
from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

logger = logging.getLogger(__name__)

# Source of the self-contained DataFrame summarizer, inlined into the sandbox
# script (the subprocess runs with PYTHONPATH="" and cannot import this package).
_SANDBOX_SUMMARY_SRC = (
    Path(__file__).resolve().parent.parent / "sampling" / "sandbox_summary.py"
).read_text(encoding="utf-8")

_spawn_subprocess = asyncio.create_subprocess_exec

# Best-effort resource caps applied to the one-shot (stateless) subprocess via
# ``preexec_fn``. Generous enough for legitimate one-shot analyses, tight enough
# to bound a runaway (disk fill, CPU spin). These apply ONLY to the stateless
# fallback path; the default persistent-kernel path is uncapped (legit large
# exports go through it). See ADR 0008 for the full rationale and the macOS
# caveat (RLIMIT_AS is a no-op on Darwin; only FSIZE/CPU are enforced there).
_RLIMIT_FSIZE_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB cap on a single file written
_RLIMIT_AS_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB address-space cap (Linux only)
# Cap on a single image read into the PARENT process memory (the sandbox can
# write a huge image; reading it verbatim here would OOM the agent). Oversized
# images are skipped with a note rather than read.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _apply_rlimits(cpu_seconds: int) -> None:
    """Set resource limits in the child process (called via ``preexec_fn``).

    Must never raise — an exception in ``preexec_fn`` aborts the spawn. Each
    limit is therefore independent and failure-tolerant: RLIMIT_FSIZE and
    RLIMIT_CPU are enforced on both macOS and Linux; RLIMIT_AS is Linux-only
    (Darwin refuses to lower it once shared libraries are mapped into the
    address space, so on macOS it silently degrades to a no-op).
    """

    fsize = _RLIMIT_FSIZE_BYTES
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))


class PythonAnalysisTool(Tool):
    """Execute Python code for data analysis in a restricted subprocess."""

    DEFAULT_TIMEOUT = 30
    MAX_TIMEOUT = 60

    # Pre-installed libraries available in the execution environment
    PREINSTALLED_LIBS = ["pandas", "numpy", "matplotlib", "seaborn", "plotly"]

    def __init__(
        self,
        allowed_paths: list[str | Path] | None = None,
        default_timeout: int = DEFAULT_TIMEOUT,
        max_timeout: int = MAX_TIMEOUT,
        sampling_config: SamplingConfig | None = None,
        kernel: KernelManager | None = None,
    ) -> None:
        self.allowed_paths = [
            Path(path).expanduser().resolve() for path in (allowed_paths or [Path.cwd()])
        ]
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        self.sampling_config = sampling_config or SamplingConfig()
        # Persistent kernel (state survives across calls). None -> stateless
        # one-shot subprocess per call, the permanent fallback path.
        self.kernel = kernel
        self._kernel_disabled = False

    @property
    def name(self) -> str:
        return "python_analysis"

    @property
    def description(self) -> str:
        return (
            "Execute Python code for data analysis. "
            "Available libraries: pandas, numpy, matplotlib, seaborn, plotly. "
            "Use this for data transformation, statistical analysis, and visualization."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Maximum execution time in seconds (default {self.default_timeout})",
                },
            },
            "required": ["code"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return False

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        code = input_data.get("code")
        if not code or not isinstance(code, str):
            return ValidationResult.fail("code is required and must be a string")

        timeout = input_data.get("timeout", self.default_timeout)
        if not isinstance(timeout, int) or isinstance(timeout, bool):
            return ValidationResult.fail("timeout must be an integer")
        if timeout < 1 or timeout > self.max_timeout:
            return ValidationResult.fail(
                f"timeout must be between 1 and {self.max_timeout} seconds"
            )

        # Layer 1: Simple string blocklist
        blocked = [
            "__import__(",
            "eval(",
            "exec(",
            "compile(",
            "os.system",
            "os.popen",
            "subprocess.",
            "import socket",
            "import urllib",
            "import ftplib",
            "import smtplib",
            "import telnetlib",
            "import requests",
            "open('/etc",
            "open('/proc",
            "open('/sys",
            "open('/dev",
        ]
        for pattern in blocked:
            if pattern in code:
                return ValidationResult.fail(f"Code contains potentially unsafe pattern: {pattern}")

        # Layer 2: AST-based static analysis
        ast_error = self._validate_ast(code)
        if ast_error:
            return ValidationResult.fail(ast_error)

        return ValidationResult.success()

    def _validate_ast(self, code: str) -> str | None:
        """Parse code with AST and reject dangerous constructs.

        Never raises: a validator bug surfaces as a fail-closed validation
        failure rather than an uncaught exception in the agent loop. (A crash
        on ``Path()`` once propagated out and killed a turn — see
        ``tests/test_sandbox_hardening.py``.)
        """
        try:
            return self._analyze_ast(code)
        except SyntaxError as e:
            return f"Syntax error: {e}"
        except Exception as e:  # pragma: no cover - defensive; never bypass
            logger.exception("AST validator crashed (fail-closed)")
            return f"Code rejected: validator error ({type(e).__name__})"

    def _analyze_ast(self, code: str) -> str | None:
        tree = ast.parse(code)  # SyntaxError propagates to _validate_ast

        # Dangerous imports and calls to block.
        #
        # NOTE: this is an AST *blacklist*, which is bypassable by construction
        # (see ADR 0008). It is best-effort containment of model-generated code
        # for the single-user local CLI threat model — NOT a security boundary
        # against an adversarial tenant. The list below closes the cheap,
        # well-known escape routes; it cannot be made complete.
        dangerous_imports = {
            "os",
            "sys",
            "subprocess",
            "socket",
            "urllib",
            "urllib2",
            "http",
            "ftplib",
            "smtplib",
            "telnetlib",
            "requests",
            "shutil",
            # dynamic-import / FFI escape hatches: reachable via the blocked
            # ``__import__`` but added explicitly so a clever alias
            # (``import importlib``) is rejected at the import statement.
            "importlib",
            "ctypes",
            "multiprocessing",
            "builtins",
            "pickle",
            "marshal",
            # ``operator.methodcaller``/``attrgetter`` reach methods/attributes
            # by STRING, so they bypass every ast.Attribute check below (e.g.
            # methodcaller('unlink')(path) reopens the closed file-method class).
            # The ``operator`` module has near-zero legitimate data-analysis use.
            "operator",
            # ``inspect.currentframe().f_builtins`` is a full arbitrary-code
            # escape (live builtins dict → __import__ → os). Blocked here at the
            # source; the frame-attribute sink (f_builtins/gi_frame/...) is also
            # blocked in the Attribute branch as belt-and-braces.
            "inspect",
            # Process-spawn / REPL-host modules: not plausible in analysis code,
            # zero over-blocking risk, blocked as cheap defense-in-depth.
            "pty",
            "runpy",
            "code",
            "pdb",
        }
        # Names that must not be *referenced at all* (not only called): an
        # alias like ``g = getattr; g(...)`` or ``b = __builtins__`` defeats a
        # call-only check. ``getattr``/``setattr``/``delattr`` are the classic
        # dynamic-attribute family — ``getattr(builtins, "ev"+"al")`` string-
        # concat-bypasses the Layer-1 substring blocklist, and ``setattr(x,
        # "__class__", ...)`` reaches the dunder layer without an attribute
        # access the dunder check can see. ``eval``/``exec``/``compile``/
        # ``__import__`` are listed here (not a separate call-set) so both the
        # call and any alias are rejected at the Name check.
        dangerous_names = {
            # ``_wrap_code`` preamble does ``import sys`` for stdout capture and
            # leaks the name into user scope; ``sys.modules['os'].system(...)``
            # is a full user-level ACE that ``import sys`` blocking alone does
            # not stop. Block the Name reference too (``import sys`` is already
            # in dangerous_imports). See ADR 0008 + test_sandbox_hardening.
            "sys",
            "getattr",
            "setattr",
            "delattr",
            "globals",
            "locals",
            "vars",
            "__builtins__",
            "__import__",
            "eval",
            "exec",
            "compile",
        }
        dangerous_path_methods = {
            "open",
            "read_text",
            "read_bytes",
            "write_text",
            "write_bytes",
            "unlink",
            "rmdir",
            "rename",
            "replace",
            "chmod",
        }
        data_read_calls = {"read_csv", "read_parquet", "read_excel", "read_json", "read_table"}
        # Frame / code-object attributes are the SINK every reflection→ACE
        # route funnels through: ``gen.gi_frame.f_builtins['__import__']('os')``,
        # ``inspect.currentframe().f_builtins``, ``tb_frame``, etc. Blocking the
        # sink (rather than every module that can reach a frame) is what actually
        # shrinks the class — see ADR 0008. No legitimate data-analysis code
        # touches these names.
        frame_attrs = {
            "f_builtins",
            "f_globals",
            "f_locals",
            "f_back",
            "f_code",
            "gi_frame",
            "cr_frame",
            "ag_frame",
            "tb_frame",
            # code-object attrs of generators/coroutines/threads — symmetric with
            # their *_frame siblings; no legit analysis code touches these.
            "gi_code",
            "cr_code",
            "ag_code",
            "tb_next",
        }

        # ``open`` may be CALLED directly (its path is then whitelist-checked by
        # _validate_file_call) but never aliased or passed: ``f = open;
        # f('/etc/passwd')`` would invoke the alias, which the Call branch below
        # does not recognize, skipping the path whitelist entirely — the same
        # aliasing class as getattr. Collect Name nodes used directly as a
        # Call's function; any other reference to ``open`` is rejected.
        direct_call_func_ids = {
            id(n.func)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in dangerous_names:
                return f"Reference to '{node.id}' is not allowed"
            if (
                isinstance(node, ast.Name)
                and node.id == "open"
                and id(node) not in direct_call_func_ids
            ):
                return "Reference to 'open' is not allowed (use a direct open(...) call)"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    base = alias.name.split(".")[0]
                    if base in dangerous_imports:
                        return f"Import of '{alias.name}' is not allowed"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    base = node.module.split(".")[0]
                    if base in dangerous_imports:
                        return f"Import from '{node.module}' is not allowed"
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    return self._validate_file_call("open", node)
                if isinstance(node.func, ast.Name) and node.func.id == "Path":
                    err = self._validate_first_path_argument("Path", node)
                    if err:
                        return err
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in dangerous_path_methods:
                        return f"Direct filesystem method '{node.func.attr}' is not allowed"
                    if node.func.attr in data_read_calls and node.args:
                        err = self._validate_first_path_argument(node.func.attr, node)
                        if err:
                            return err
            elif isinstance(node, ast.Attribute):
                # Filesystem methods are blocked even as a bare reference, not
                # only as a direct call: ``f = io.open; f(path)`` or
                # ``g = Path(x).read_bytes; g()`` would otherwise skip the
                # Call-branch check (the attribute is the RHS of an assignment
                # or a standalone expression, never a Call.func). Direct calls
                # are already rejected in the Call branch above, so rejecting
                # the reference form too loses no legitimate use.
                if node.attr in dangerous_path_methods:
                    return f"Reference to filesystem method '{node.attr}' is not allowed"
                if node.attr in frame_attrs:
                    return f"Reference to frame attribute '{node.attr}' is not allowed"
                if node.attr.startswith("__"):
                    return f"Access to dunder attribute '{node.attr}' is not allowed"
        return None

    def _validate_file_call(self, call_name: str, node: ast.Call) -> str | None:
        """Validate direct file API calls fail closed."""
        if not node.args:
            return f"{call_name}() requires a path argument"
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            return f"{call_name}() path must be a literal relative path or allowed absolute path"
        return self._validate_path_literal(call_name, first_arg.value)

    def _validate_first_path_argument(self, call_name: str, node: ast.Call) -> str | None:
        """Validate absolute path literals passed to data/file helpers."""
        if not node.args:
            # Zero-arg forms (e.g. ``Path()`` for the cwd) have no path to check.
            return None
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            return self._validate_path_literal(call_name, first_arg.value)
        return None

    def _validate_path_literal(self, call_name: str, raw_path: str) -> str | None:
        """Allow relative paths and configured absolute paths; deny everything else."""
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            return None

        resolved = path.resolve(strict=False)
        for allowed in self.allowed_paths:
            if resolved == allowed or resolved.is_relative_to(allowed):
                return None
        return f"{call_name}() path is outside allowed analysis paths: {raw_path}"

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        # Invariant: the framework (tool gate) runs ``validate_input`` before
        # dispatching here, so the AST/substring sandbox has already applied.
        # ``call()`` intentionally does NOT re-validate: kernel-crash regression
        # tests inject raw crash-inducing code (e.g. ``os._exit``) through this
        # boundary on purpose. Any caller going directly through ``call()`` must
        # validate first.
        code = input_data["code"]
        timeout = input_data.get("timeout", self.default_timeout)

        if self.kernel is not None and not self._kernel_disabled:
            return await self._call_kernel(code, timeout)
        return await self._call_stateless(code, timeout)

    async def _call_kernel(self, code: str, timeout: int) -> ToolResult:
        """Run in the persistent kernel; degrade per the robustness chain.

        start-failure -> permanent stateless fallback; timeout/crash ->
        restart + explicit "variables lost" notice so the model can rebuild.
        """
        assert self.kernel is not None
        try:
            kres = await self.kernel.execute(code, timeout)
        except KernelStartError as e:
            # Permanent downgrade to stateless: every later python_analysis loses
            # cross-call state. This is a behavior change — surface it, once.
            logger.warning("persistent kernel unavailable, falling back to stateless: %r", e)
            self._kernel_disabled = True
            return await self._call_stateless(code, timeout)
        except KernelTimeoutError:
            await self._safe_restart()
            return ToolResult(
                content=(
                    f"Execution timed out after {timeout} seconds. "
                    "[kernel restarted; session variables lost]"
                ),
                is_error=True,
            )
        except KernelCrashError as e:
            await self._safe_restart()
            return ToolResult(
                content=f"Kernel crashed: {e} [kernel restarted; session variables lost]",
                is_error=True,
            )
        stderr_text = "\n".join(part for part in (kres.stderr, kres.error) if part)
        return self._compose_result(
            kres.stdout, stderr_text, kres.outputs, is_error=kres.error is not None
        )

    async def _safe_restart(self) -> None:
        assert self.kernel is not None
        try:
            await self.kernel.restart()
        except KernelError as e:
            logger.warning("kernel restart failed, downgrading to stateless: %r", e)
            self._kernel_disabled = True

    async def _call_stateless(self, code: str, timeout: int) -> ToolResult:
        """One-shot subprocess execution (original behavior; kernel fallback)."""
        wrapped_code = self._wrap_code(code)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(wrapped_code)
            temp_path = f.name

        # Run in isolated temp directory to restrict file-system visibility
        cwd = tempfile.mkdtemp(prefix="agent_sandbox_")
        try:
            proc = await _spawn_subprocess(
                sys.executable,
                temp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={
                    "PYTHONPATH": "",
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": cwd,
                    "TMPDIR": cwd,
                },
                # Best-effort caps on disk/CPU/address-space. No-op on the
                # persistent-kernel path, which is bounded per-request by the
                # manager's wall-clock timeout and the OS OOM killer instead
                # (a persistent REPL legitimately accumulates large state).
                preexec_fn=lambda: _apply_rlimits(timeout + 10),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(
                    content=f"Execution timed out after {timeout} seconds",
                    is_error=True,
                )

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            outputs: list[dict[str, Any]] = []
            for line in stdout_text.splitlines():
                if line.startswith("__AGENT_RESULT__:"):
                    with suppress(json.JSONDecodeError):
                        structured = json.loads(line[len("__AGENT_RESULT__:") :])
                        if isinstance(structured, dict):
                            outs = structured.get("outputs", [])
                            if isinstance(outs, list):
                                outputs.extend(o for o in outs if isinstance(o, dict))

            return self._compose_result(
                stdout_text, stderr_text, outputs, is_error=proc.returncode != 0
            )

        finally:
            with suppress(OSError):
                os.unlink(temp_path)
            with suppress(OSError):
                shutil.rmtree(cwd, ignore_errors=True)

    def _compose_result(
        self,
        stdout_text: str,
        stderr_text: str,
        outputs: list[dict[str, Any]],
        is_error: bool,
    ) -> ToolResult:
        """Build the ToolResult shared by kernel and stateless paths."""
        clean_stdout = self._clean_stdout(stdout_text)
        result_parts = []
        if clean_stdout:
            result_parts.append(f"[stdout]\n{clean_stdout}")
        if stderr_text:
            result_parts.append(f"[stderr]\n{stderr_text}")

        if not outputs:
            return ToolResult(
                content="\n\n".join(result_parts) or "Execution completed with no output.",
                is_error=is_error,
            )

        images = []
        for item in outputs:
            if item.get("type") == "image":
                img_path = item.get("path")
                if img_path and Path(img_path).exists():
                    # Cap the read so a runaway image in the sandbox cannot OOM
                    # the parent process; skip oversized images with a note.
                    size = Path(img_path).stat().st_size
                    if size > _MAX_IMAGE_BYTES:
                        result_parts.append(
                            f"[image skipped: {Path(img_path).name} is {size} bytes > "
                            f"{_MAX_IMAGE_BYTES} cap; save a smaller copy]"
                        )
                        continue
                    with open(img_path, "rb") as img:
                        b64 = base64.b64encode(img.read()).decode()
                    # path lets the artifact seam reuse an already-delivered
                    # file instead of writing a duplicate copy.
                    images.append(
                        {"format": item.get("format", "png"), "data": b64, "path": str(img_path)}
                    )

        metadata: dict[str, Any] = {"structured": {"outputs": outputs}}
        if images:
            metadata["images"] = images

        table_summaries = [
            item["summary"]
            for item in outputs
            if item.get("type") == "table_summary" and "summary" in item
        ]
        if table_summaries:
            rendered = "\n\n".join(
                render.render_summary_dict(summary) for summary in table_summaries
            )
            content = (
                f"{clean_stdout}\n\n{rendered}"
                if clean_stdout and len(clean_stdout) < 2000
                else rendered
            )
            if stderr_text:
                content += f"\n\n[stderr]\n{stderr_text}"
            return ToolResult(content=content, metadata=metadata, is_error=is_error)

        return ToolResult(
            content="\n\n".join(result_parts) or "Execution completed.",
            metadata=metadata,
            is_error=is_error,
        )

    @staticmethod
    def _clean_stdout(stdout_text: str) -> str:
        """Drop the structured-result marker and summarizer notices from stdout."""
        kept = [
            line
            for line in stdout_text.splitlines()
            if not line.startswith("__AGENT_RESULT__:") and not line.startswith("[agent_summarize]")
        ]
        return "\n".join(kept).strip()

    def _wrap_code(self, user_code: str) -> str:
        """Wrap user code with stdout capture + the inlined DataFrame summarizer.

        ``agent_summarize(df)`` is exposed to user code; additionally, a trailing
        hook auto-summarizes a large ``result`` DataFrame/Series so its full print
        does not flood the context. Both degrade to no-ops without pandas.
        """
        cfg = self.sampling_config
        params = (
            f"max_sample_rows={cfg.max_sample_rows}, top_k={cfg.top_k}, "
            f"quantiles={tuple(cfg.quantiles)!r}, stratify={cfg.stratify!r}, "
            f"include_outliers={cfg.include_outliers}, "
            f"max_outlier_rows={cfg.max_outlier_rows}, seed={cfg.seed}"
        )
        preamble = (
            "import sys\n"
            "import json\n"
            "import random\n"
            "\n"
            "_original_stdout = sys.stdout\n"
            "_output_buffer = []\n"
            "\n"
            "class _CaptureStdout:\n"
            "    def write(self, text):\n"
            "        _output_buffer.append(text)\n"
            "        _original_stdout.write(text)\n"
            "    def flush(self):\n"
            "        _original_stdout.flush()\n"
            "\n"
            "sys.stdout = _CaptureStdout()\n"
            "\n"
            "def agent_result(outputs):\n"
            '    print("__AGENT_RESULT__:" + json.dumps({"outputs": outputs}))\n'
        )
        glue = (
            "\n\n"
            "def agent_summarize(obj):\n"
            "    try:\n"
            f"        _summary = summarize_dataframe(obj, {params})\n"
            '        agent_result([{"type": "table_summary", "summary": _summary}])\n'
            '        print("[agent_summarize] summarized %d rows x %d cols"\n'
            '              % (_summary["n_rows"], _summary["n_cols"]))\n'
            "        return _summary\n"
            "    except Exception as _exc:\n"
            '        print("[agent_summarize] skipped: %s" % _exc)\n'
            "        return None\n"
        )
        auto_hook = (
            "\n\n"
            "try:\n"
            "    _agent_auto_result = result\n"
            "except NameError:\n"
            "    _agent_auto_result = None\n"
            "if _agent_auto_result is not None:\n"
            "    try:\n"
            "        import pandas as _agent_pd\n"
            "        if isinstance(_agent_auto_result, (_agent_pd.DataFrame, _agent_pd.Series)) \\\n"
            f"                and int(_agent_auto_result.shape[0]) > {cfg.trigger_rows}:\n"
            "            agent_summarize(_agent_auto_result)\n"
            "    except Exception:\n"
            "        pass\n"
        )
        return (
            preamble
            + "\n# --- injected sandbox summarizer ---\n"
            + _SANDBOX_SUMMARY_SRC
            + glue
            + "\n# --- user code ---\n"
            + user_code
            + auto_hook
        )
