"""Base Tool interface and related types.

Fail-closed by default:
- is_concurrency_safe defaults to False
- is_read_only defaults to False
- is_destructive defaults to True
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Result of a tool execution."""

    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Input validation outcome."""

    valid: bool = True
    error: str = ""

    @classmethod
    def success(cls) -> ValidationResult:
        return cls(valid=True)

    @classmethod
    def fail(cls, error: str) -> ValidationResult:
        return cls(valid=False, error=error)


@dataclass
class PermissionResult:
    """Permission check outcome."""

    allowed: bool = False
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> PermissionResult:
        return cls(allowed=True, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> PermissionResult:
        return cls(allowed=False, reason=reason)


# Callback type: tool asks harness whether it may proceed
CanUseToolFn = Callable[["Tool", dict[str, Any]], PermissionResult]


class Tool(ABC):
    """Abstract base class for all tools.

    Security properties are per-invocation: the same tool may be safe
    with one input and unsafe with another.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name (snake_case)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the model."""

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema describing the tool's input parameters."""

    @property
    def max_result_size_chars(self) -> int:
        """Maximum result size before truncation / persistence."""
        return 50_000

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        """Whether this invocation can run in parallel with others.

        Fail-closed: default False (serial execution).
        """
        return False

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        """Whether this invocation is read-only.

        Fail-closed: default False (treated as write operation).
        """
        return False

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        """Whether this invocation is irreversible.

        Fail-closed: default True (treated as destructive).
        """
        return True

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        """Validate input against schema and semantic constraints."""
        return ValidationResult.success()

    def check_permissions(self, input_data: dict[str, Any]) -> PermissionResult:
        """Tool-level permission check (paths, dangerous patterns, etc.)."""
        return PermissionResult.allow()

    @abstractmethod
    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        """Execute the tool."""

    def to_anthropic_tool(self) -> dict[str, Any]:
        """Convert to Anthropic API tool definition format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
