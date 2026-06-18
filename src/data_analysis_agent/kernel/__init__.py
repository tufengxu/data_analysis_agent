"""Persistent analysis kernel: session-scoped Python execution with state.

``manager`` is the harness side; ``kernel_main`` is the sandbox-side REPL
source (never imported at runtime — composed into a boot script and spawned).
"""

from __future__ import annotations

from .manager import (
    KernelCrashError,
    KernelError,
    KernelManager,
    KernelResult,
    KernelStartError,
    KernelTimeoutError,
)

__all__ = [
    "KernelCrashError",
    "KernelError",
    "KernelManager",
    "KernelResult",
    "KernelStartError",
    "KernelTimeoutError",
]
