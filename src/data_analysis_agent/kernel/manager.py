"""KernelManager: lifecycle + line-protocol I/O for the persistent kernel.

Harness side of the kernel seam. Composes the sandbox boot script
(``sandbox_summary`` source + ``kernel_main`` source), spawns it with the same
isolation env as the stateless sandbox (``PYTHONPATH=""``), and exchanges one
JSON line per request. The working directory is session-scoped: it survives
across tool calls (variables, charts, intermediate files) and is NOT removed
per execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..sampling.config import SamplingConfig

_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_SANDBOX_SUMMARY_PATH = _PACKAGE_DIR / "sampling" / "sandbox_summary.py"
_KERNEL_MAIN_PATH = Path(__file__).resolve().parent / "kernel_main.py"

_spawn_subprocess = asyncio.create_subprocess_exec


class KernelError(Exception):
    """Base class for kernel failures."""


class KernelStartError(KernelError):
    """The kernel subprocess could not be spawned."""


class KernelCrashError(KernelError):
    """The kernel subprocess died mid-request."""


class KernelTimeoutError(KernelError):
    """A request exceeded its timeout; the kernel was killed."""


@dataclass
class KernelResult:
    """One request's outcome — the fields a caller needs.

    The wire `id`/`ok` are intentionally dropped; success is derived from `error`.
    """

    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    outputs: list[dict[str, Any]] = field(default_factory=list)


class KernelManager:
    """Owns one persistent kernel subprocess and serializes requests to it."""

    # readline() limit; responses are capped kernel-side at 2MB per field.
    STREAM_LIMIT = 16 * 1024 * 1024

    def __init__(
        self,
        sampling_config: SamplingConfig | None = None,
        work_dir: str | Path | None = None,
    ) -> None:
        self.sampling_config = sampling_config or SamplingConfig()
        if work_dir is None:
            self.work_dir = Path(tempfile.mkdtemp(prefix="daa_kernel_"))
        else:
            self.work_dir = Path(work_dir)
            self.work_dir.mkdir(parents=True, exist_ok=True)
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_path = self.work_dir / "_kernel_stderr.log"

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _sampling_params(self) -> dict[str, Any]:
        cfg = self.sampling_config
        return {
            "max_sample_rows": cfg.max_sample_rows,
            "top_k": cfg.top_k,
            "quantiles": list(cfg.quantiles),
            "stratify": cfg.stratify,
            "include_outliers": cfg.include_outliers,
            "max_outlier_rows": cfg.max_outlier_rows,
            "seed": cfg.seed,
            "trigger_rows": cfg.trigger_rows,
        }

    def _compose_boot_script(self) -> str:
        summary_src = _SANDBOX_SUMMARY_PATH.read_text(encoding="utf-8")
        kernel_src = _KERNEL_MAIN_PATH.read_text(encoding="utf-8")
        # __future__ imports must be first in a file; sandbox_summary leads the
        # composed script, so strip the line (PEP 604 syntax is native on 3.10+).
        kernel_src = kernel_src.replace("from __future__ import annotations\n", "", 1)
        return summary_src + "\n\n# --- kernel REPL ---\n" + kernel_src

    async def start(self) -> None:
        """Spawn the kernel subprocess (idempotent if already alive)."""
        async with self._lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        if self.alive:
            return
        script_path = self.work_dir / "_kernel_boot.py"
        try:
            script_path.write_text(self._compose_boot_script(), encoding="utf-8")
            # "wb": one spawn, one log — keeps the file from growing without
            # bound across restarts (the tail is read before any restart).
            with open(self._stderr_path, "wb") as stderr_file:
                self._proc = await _spawn_subprocess(
                    sys.executable,
                    str(script_path),
                    json.dumps(self._sampling_params()),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=stderr_file,
                    cwd=str(self.work_dir),
                    env={
                        "PYTHONPATH": "",
                        "PATH": os.environ.get("PATH", ""),
                        "HOME": str(self.work_dir),
                        "TMPDIR": str(self.work_dir),
                        "MPLBACKEND": "Agg",
                    },
                    limit=self.STREAM_LIMIT,
                )
        except OSError as e:
            raise KernelStartError(f"failed to start kernel: {e}") from e

    async def execute(self, code: str, timeout: float) -> KernelResult:
        """Run one code block; raises KernelTimeoutError / KernelCrashError.

        On timeout or crash the process is killed and state is lost; callers
        decide whether to restart (see PythonAnalysisTool's degradation chain).
        """
        async with self._lock:
            if not self.alive:
                await self._start_locked()
            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None

            payload = json.dumps({"id": uuid.uuid4().hex[:8], "code": code}, ensure_ascii=False)
            try:
                proc.stdin.write(payload.encode("utf-8") + b"\n")
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                await self._kill()
                raise KernelCrashError(f"kernel pipe closed: {e}. {self._stderr_tail()}") from e

            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError as e:
                await self._kill()
                raise KernelTimeoutError(f"Execution timed out after {timeout} seconds") from e
            except (ValueError, asyncio.LimitOverrunError) as e:
                # Response line exceeded STREAM_LIMIT; the stream is now
                # desynced (partial line consumed), so treat it as a crash.
                await self._kill()
                raise KernelCrashError(f"kernel response exceeded stream limit: {e}") from e

            if not line:
                await self._kill()
                raise KernelCrashError(f"kernel process exited unexpectedly. {self._stderr_tail()}")
            try:
                response = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as e:
                await self._kill()
                raise KernelCrashError("kernel protocol desync (non-JSON response)") from e

            return KernelResult(
                stdout=str(response.get("stdout", "")),
                stderr=str(response.get("stderr", "")),
                error=response.get("error"),
                outputs=[o for o in (response.get("outputs") or []) if isinstance(o, dict)],
            )

    async def restart(self) -> None:
        """Kill (if needed) and respawn; persistent variables are lost."""
        async with self._lock:
            await self._kill_locked()
            await self._start_locked()

    async def shutdown(self) -> None:
        """Kill the subprocess. The work_dir is kept: artifacts live there."""
        async with self._lock:
            await self._kill_locked()

    async def _kill(self) -> None:
        """Kill from inside execute() (lock already held)."""
        await self._kill_locked()

    async def _kill_locked(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()

    def _stderr_tail(self, max_chars: int = 2000) -> str:
        try:
            text = self._stderr_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        tail = text[-max_chars:].strip()
        return f"stderr tail: {tail}" if tail else ""
