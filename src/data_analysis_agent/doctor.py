"""Health checks for the local DataAnalysisAgent install.

``data-agent doctor`` runs a battery of read-only checks and prints a pass/warn/
fail report. It never mutates state beyond creating and removing a probe file to
test writability, and one check never aborts the others — each is wrapped so the
report is always complete.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path

from .config import AgentConfig

# Soft heuristic thresholds for the disk-usage warnings. Trajectories are capped
# at 512 MiB by TrajectoryLogger; warn as a run approaches that ceiling.
_TRAJECTORY_WARN_BYTES = 400 * 1024 * 1024
_TOTAL_WARN_BYTES = 1024 * 1024 * 1024
_DISK_SUBDIRS = ("trajectories", "memory", "skills", "eval_tasks", "projects")

PASS = "pass"
WARN = "warn"
FAIL = "fail"
INFO = "info"


@dataclass
class CheckResult:
    """One doctor finding. ``detail`` is a short human-readable line."""

    name: str
    status: str  # PASS | WARN | FAIL | INFO
    detail: str


def check_all(config: AgentConfig | None = None) -> list[CheckResult]:
    """Run every doctor check against the given (or env-derived) config."""
    config = config or AgentConfig.from_env()
    return [
        _check_api_key(config),
        _check_data_extras(),
        _check_daa_home(config),
        *_check_disk_usage(config),
        _check_echarts(config),
        _check_permission(config),
        _check_kernel_python(),
        _check_web_port(),
    ]


def _check_api_key(config: AgentConfig) -> CheckResult:
    if config.api_key:
        return CheckResult("API key", PASS, "ANTHROPIC_API_KEY is set.")
    return CheckResult("API key", FAIL, "ANTHROPIC_API_KEY not set; runs will refuse to start.")


def _check_data_extras() -> CheckResult:
    try:
        import pandas  # noqa: F401
    except Exception:  # ImportError, ABI-mismatch RuntimeError, etc. — never abort the report.
        return CheckResult(
            "Data extras",
            WARN,
            "pandas not importable; install with `uv sync --extra data` "
            "(core runs but data tools degrade).",
        )
    return CheckResult("Data extras", PASS, "pandas importable; [data] extras installed.")


def _check_daa_home(config: AgentConfig) -> CheckResult:
    home = config.daa_home()
    # PID-unique probe so concurrent doctor runs don't collide; cleaned up in finally
    # even when the write itself fails (the report should never leave litter behind).
    probe = home / f".doctor_probe.{os.getpid()}"
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        return CheckResult("DAA_HOME writable", FAIL, f"{home} not writable: {exc}")
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()
    return CheckResult("DAA_HOME writable", PASS, str(home))


def _check_disk_usage(config: AgentConfig) -> list[CheckResult]:
    """Per-subdir size breakdown + warnings near the trajectory cap or a total ceiling."""
    home = config.daa_home()
    results: list[CheckResult] = []
    for sub in _DISK_SUBDIRS:
        sub_path = home / sub
        size = _dir_size(sub_path) if sub_path.is_dir() else 0
        if sub == "trajectories" and size > _TRAJECTORY_WARN_BYTES:
            results.append(
                CheckResult(
                    f"disk · {sub}",
                    WARN,
                    f"{_mb(size)} MiB — approaching the 512 MiB trajectory cap; "
                    "old sessions are auto-evicted but review retention.",
                )
            )
        else:
            results.append(CheckResult(f"disk · {sub}", INFO, f"{_mb(size)} MiB"))
    # True total over the whole ~/.daa tree (not just the listed subdirs).
    true_total = _dir_size(home) if home.is_dir() else 0
    status = WARN if true_total > _TOTAL_WARN_BYTES else INFO
    results.append(CheckResult("disk · total ~/.daa", status, f"{_mb(true_total)} MiB"))
    return results


def _check_echarts(config: AgentConfig) -> CheckResult:
    src = config.echarts_src
    if src.startswith(("http://", "https://")):
        return CheckResult("ECharts", INFO, f"CDN (<script src>): {src} (reports need network).")
    if src:
        if Path(src).expanduser().is_file():
            return CheckResult("ECharts", PASS, f"local inline: {src} (offline-ready reports).")
        return CheckResult(
            "ECharts", WARN, f"local path not found: {src} (reports will lack charts)."
        )
    return CheckResult("ECharts", WARN, "echarts_src empty; reports will lack charts.")


def _check_permission(config: AgentConfig) -> CheckResult:
    if config.permission_preset:
        return CheckResult("Permission", INFO, f"preset={config.permission_preset}")
    return CheckResult("Permission", INFO, f"mode={config.permission_mode} (no preset)")


def _check_kernel_python() -> CheckResult:
    python = shutil.which("python3") or shutil.which("python")
    if python:
        return CheckResult("Kernel python", PASS, f"found {python}")
    return CheckResult(
        "Kernel python", FAIL, "no python3/python on PATH; persistent kernel cannot start."
    )


def _check_web_port() -> CheckResult:
    """Best-effort: is the default local Web port free? (Wave 2 prep, advisory.)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 8000))
        return CheckResult(
            "Web port :8000", PASS, "127.0.0.1:8000 is free (local workbench ready)."
        )
    except OSError:
        return CheckResult(
            "Web port :8000",
            WARN,
            "127.0.0.1:8000 in use (Wave 2 workbench will need another port).",
        )
    finally:
        s.close()


def _dir_size(path: Path) -> int:
    """Bytes consumed under ``path``. Uses lstat so symlinks do not pull in size
    from targets outside the tree (file symlinks count their own small size;
    dir symlinks are not followed by os.walk, matching that semantics)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f}"
