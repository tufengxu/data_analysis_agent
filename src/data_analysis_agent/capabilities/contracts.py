"""Harness-agnostic capability contracts (v2 capability core).

A *capability* is a named, LLM-agnostic unit of analysis work: read a table,
render a report, run a causal readout, compact a tool result. This module
defines the shape every capability must declare and the fail-closed execution
envelope that serves them identically in-process, over MCP stdio, and via the
CLI (transport-consistency invariant).

Dependency direction (drift-enforced): ``capabilities.*`` may never import the
v1 harness internals (agent_loop / session / state_machine / protocol / events
/ runtime / ...) nor any base-harness code; capability-side v1 packages
(``tools`` / ``kernel`` / ``artifacts`` / ...) may be delegated to.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

CAPABILITY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

#: Canonical error codes (fail-closed vocabulary shared by every transport).
ERROR_CODES = (
    "validation_error",
    "permission_denied",
    "not_found",
    "unavailable",
    "execution_error",
)


class Permission(str, Enum):
    """What a capability needs from its environment (adapters map this onto
    base-harness approval mechanisms; the capability layer itself fail-closes
    on artifact paths and executes_code declarations)."""

    READ_ONLY = "read_only"
    WRITES_ARTIFACTS = "writes_artifacts"
    EXECUTES_CODE = "executes_code"


class OutputKind(str, Enum):
    """Coarse result shape, for adapter-side rendering decisions."""

    TEXT = "text"
    STRUCTURED = "structured"
    ARTIFACT = "artifact"


@dataclass(frozen=True)
class CapabilitySpec:
    """Declarative contract of one capability.

    ``input_schema`` is a JSON Schema (object) describing the input; handlers
    validate against it semantically and raise ``CapabilityError`` with code
    ``validation_error`` on bad input — the executor never leaks tracebacks.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    domain: str
    output_kind: OutputKind = OutputKind.TEXT
    permission: Permission = Permission.READ_ONLY
    error_codes: tuple[str, ...] = ("execution_error",)

    def __post_init__(self) -> None:
        if not CAPABILITY_NAME_RE.match(self.name):
            raise ValueError(f"capability name invalid: {self.name!r}")
        if not self.description or not self.description.strip():
            raise ValueError(f"capability {self.name}: description required")
        if not isinstance(self.input_schema, dict) or self.input_schema.get("type") != "object":
            raise ValueError(f"capability {self.name}: input_schema must be a JSON object schema")
        for code in self.error_codes:
            if code not in ERROR_CODES:
                raise ValueError(f"capability {self.name}: unknown error code {code!r}")

    def to_public_dict(self) -> dict[str, Any]:
        """Serializable contract view (served by MCP/CLI ``list``)."""

        return {
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "input_schema": self.input_schema,
            "output_kind": self.output_kind.value,
            "permission": self.permission.value,
            "error_codes": list(self.error_codes),
        }


@dataclass(frozen=True)
class CapabilityOutput:
    """Successful handler result. ``content`` is the model-facing text;
    ``data``/``metadata`` are structured companions; ``artifacts`` are real
    delivered paths (upstream of each base's artifact reporting channel)."""

    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()


class CapabilityError(Exception):
    """Expected, declared failure. Codes come from :data:`ERROR_CODES`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code if code in ERROR_CODES else "execution_error"
        self.message = message


CapabilityHandler = Callable[[dict[str, Any]], Awaitable[CapabilityOutput]]


def success_envelope(output: CapabilityOutput, capability: str) -> dict[str, Any]:
    return {
        "ok": True,
        "capability": capability,
        "content": output.content,
        "data": output.data,
        "artifacts": list(output.artifacts),
        "metadata": output.metadata,
    }


def error_envelope(capability: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "capability": capability,
        "error": {"code": code, "message": message},
    }


class CapabilityRegistry:
    """Name → (spec, handler). Assembled per-process by the serving layer;
    in-process callers may equally build scoped registries for tests."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[CapabilitySpec, CapabilityHandler]] = {}

    def register(self, spec: CapabilitySpec, handler: CapabilityHandler) -> None:
        if spec.name in self._entries:
            raise ValueError(f"duplicate capability registration: {spec.name}")
        self._entries[spec.name] = (spec, handler)

    def specs(self) -> list[CapabilitySpec]:
        return [entry[1][0] for entry in sorted(self._entries.items())]

    def get(self, name: str) -> CapabilitySpec:
        try:
            return self._entries[name][0]
        except KeyError:
            raise CapabilityError("not_found", f"unknown capability: {name}") from None

    def has(self, name: str) -> bool:
        return name in self._entries

    async def execute(self, name: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """Fail-closed execution envelope (single source of truth for all
        transports: MCP, CLI, and direct in-process calls).

        Any exception becomes a structured error — never a crash, never a
        traceback in the model-facing output.
        """
        try:
            if not self.has(name):
                raise CapabilityError("not_found", f"unknown capability: {name}")
            if not isinstance(input_data, dict):
                raise CapabilityError("validation_error", "input must be a JSON object mapping")
            _, handler = self._entries[name]
            output = await handler(input_data)
        except CapabilityError as failure:
            return error_envelope(name, failure.code, failure.message)
        except Exception as exc:  # noqa: BLE001 — fail-closed envelope by contract
            return error_envelope(name, "execution_error", f"{type(exc).__name__}: {exc}")
        return success_envelope(output, name)

    # Transport-facing alias: serving layers (MCP/CLI/adapters) prefer the verb
    # `dispatch` at the seam; identical fail-closed envelope.
    dispatch = execute
