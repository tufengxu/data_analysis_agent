"""Permission engine implementing deny-first security model.

Four-layer defense:
1. Rule Layer    - static declarative rules (deny > ask > allow)
2. Decision Layer - runtime semantic checks per tool
3. Interaction Layer - human confirmation when needed
4. Isolation Layer - OS-level sandbox as final barrier
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class PermissionBehavior(Enum):
    ALLOW = auto()
    DENY = auto()
    ASK = auto()
    PASSTHROUGH = auto()


class PermissionMode(Enum):
    DEFAULT = auto()
    PLAN = auto()  # Read-only, no modifications
    AUTO = auto()  # Auto-approve safe operations
    BYPASS = auto()  # Skip checks (dangerous, for testing only)


@dataclass
class PermissionRule:
    """A single permission rule."""

    tool_pattern: str
    action: PermissionBehavior
    condition: str = ""  # Optional condition expression

    def matches(self, tool_name: str) -> bool:
        return fnmatch.fnmatch(tool_name, self.tool_pattern)


@dataclass
class PermissionResult:
    """Outcome of a permission check."""

    behavior: PermissionBehavior
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> PermissionResult:
        return cls(PermissionBehavior.ALLOW, reason)

    @classmethod
    def deny(cls, reason: str) -> PermissionResult:
        return cls(PermissionBehavior.DENY, reason)

    @classmethod
    def ask(cls, reason: str) -> PermissionResult:
        return cls(PermissionBehavior.ASK, reason)

    @classmethod
    def passthrough(cls) -> PermissionResult:
        return cls(PermissionBehavior.PASSTHROUGH, "No rule matched")


class PermissionEngine:
    """Core permission engine with deny-first evaluation."""

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        *,
        default_behavior: PermissionBehavior = PermissionBehavior.ASK,
    ):
        self.mode = mode
        # Fall-through behavior when no rule matches. ASK preserves the original
        # interactive posture; DENY gives deny-by-default presets (e.g. local_safe).
        self.default_behavior = default_behavior
        self.deny_rules: list[PermissionRule] = []
        self.ask_rules: list[PermissionRule] = []
        self.allow_rules: list[PermissionRule] = []

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a rule to the appropriate bucket."""
        if rule.action == PermissionBehavior.DENY:
            self.deny_rules.append(rule)
        elif rule.action == PermissionBehavior.ASK:
            self.ask_rules.append(rule)
        elif rule.action == PermissionBehavior.ALLOW:
            self.allow_rules.append(rule)

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> PermissionResult:
        """Evaluate permission for a tool invocation.

        Pipeline: deny rules -> ask rules -> tool checkPermissions -> mode check -> allow rules -> default ask
        """
        if self.mode == PermissionMode.BYPASS:
            return PermissionResult.allow("bypass mode")

        if self.mode == PermissionMode.PLAN:
            # In plan mode, we rely on the registry to filter tools; this is a safety net
            pass

        # Stage 1a: Deny rules (hard boundary)
        for rule in self.deny_rules:
            if rule.matches(tool_name):
                return PermissionResult.deny(f"Matched deny rule: {rule.tool_pattern}")

        # Stage 1b: Ask rules
        for rule in self.ask_rules:
            if rule.matches(tool_name):
                return PermissionResult.ask(f"Matched ask rule: {rule.tool_pattern}")

        # Stage 1c-e: Allow rules
        for rule in self.allow_rules:
            if rule.matches(tool_name):
                return PermissionResult.allow(f"Matched allow rule: {rule.tool_pattern}")

        # Default: fall through to the engine's configured default behavior. ASK
        # keeps the interactive posture; DENY backs deny-by-default presets.
        if self.default_behavior == PermissionBehavior.DENY:
            return PermissionResult.deny("No matching rule; denied by preset default")
        return PermissionResult.ask("No matching rule, confirmation required")
