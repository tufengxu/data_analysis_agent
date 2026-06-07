"""PythonAnalysisTool: execute Python code in a restricted subprocess.

Pre-installed: pandas, numpy, matplotlib, seaborn, plotly
Resource limits: configurable timeout (default 30s)
"""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..sampling import render
from ..sampling.config import SamplingConfig
from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

# Source of the self-contained DataFrame summarizer, inlined into the sandbox
# script (the subprocess runs with PYTHONPATH="" and cannot import this package).
_SANDBOX_SUMMARY_SRC = (
    Path(__file__).resolve().parent.parent / "sampling" / "sandbox_summary.py"
).read_text(encoding="utf-8")


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
    ) -> None:
        self.allowed_paths = [
            Path(path).expanduser().resolve() for path in (allowed_paths or [Path.cwd()])
        ]
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        self.sampling_config = sampling_config or SamplingConfig()

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
        """Parse code with AST and reject dangerous constructs."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"Syntax error: {e}"

        # Dangerous imports and calls to block
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
        }
        dangerous_calls = {"eval", "exec", "compile", "__import__"}
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

        for node in ast.walk(tree):
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
                if isinstance(node.func, ast.Name) and node.func.id in dangerous_calls:
                    return f"Call to '{node.func.id}' is not allowed"
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
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
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
        code = input_data["code"]
        timeout = input_data.get("timeout", self.default_timeout)

        wrapped_code = self._wrap_code(code)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(wrapped_code)
            temp_path = f.name

        # Run in isolated temp directory to restrict file-system visibility
        cwd = tempfile.mkdtemp(prefix="agent_sandbox_")
        try:
            proc = await asyncio.create_subprocess_exec(
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

            result_parts = []
            if stdout_text:
                result_parts.append(f"[stdout]\n{stdout_text}")
            if stderr_text:
                result_parts.append(f"[stderr]\n{stderr_text}")

            structured = None
            for line in stdout_text.splitlines():
                if line.startswith("__AGENT_RESULT__:"):
                    with suppress(json.JSONDecodeError):
                        structured = json.loads(line[len("__AGENT_RESULT__:") :])

            if structured:
                outputs = structured.get("outputs", [])
                images = []
                for item in outputs:
                    if item.get("type") == "image":
                        img_path = item.get("path")
                        if img_path and Path(img_path).exists():
                            with open(img_path, "rb") as img:
                                b64 = base64.b64encode(img.read()).decode()
                            images.append({"format": item.get("format", "png"), "data": b64})

                metadata: dict[str, Any] = {"structured": structured}
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
                    clean_stdout = self._clean_stdout(stdout_text)
                    content = (
                        f"{clean_stdout}\n\n{rendered}"
                        if clean_stdout and len(clean_stdout) < 2000
                        else rendered
                    )
                    if stderr_text:
                        content += f"\n\n[stderr]\n{stderr_text}"
                    return ToolResult(content=content, metadata=metadata)

                return ToolResult(
                    content="\n".join(result_parts) if result_parts else "Execution completed.",
                    metadata=metadata,
                )

            is_error = proc.returncode != 0
            return ToolResult(
                content="\n\n".join(result_parts)
                if result_parts
                else "Execution completed with no output.",
                is_error=is_error,
            )

        finally:
            with suppress(OSError):
                os.unlink(temp_path)
            with suppress(OSError):
                shutil.rmtree(cwd, ignore_errors=True)

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
