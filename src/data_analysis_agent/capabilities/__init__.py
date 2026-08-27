"""v2 capability core — harness-agnostic analysis capabilities.

Six capability domains (tabular / reporting / causal / evolution / sampling /
serving) live under this package. Nothing here may import the v1 harness
internals or any base-harness code (drift-enforced); the serving layer exposes
every capability over MCP stdio and the CLI with equivalent semantics.
"""

from .contracts import (
    CapabilityError,
    CapabilityHandler,
    CapabilityOutput,
    CapabilityRegistry,
    CapabilitySpec,
    OutputKind,
    Permission,
)

__all__ = [
    "CapabilityError",
    "CapabilityHandler",
    "CapabilityOutput",
    "CapabilityRegistry",
    "CapabilitySpec",
    "OutputKind",
    "Permission",
]
