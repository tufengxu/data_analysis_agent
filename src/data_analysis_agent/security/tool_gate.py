"""ToolGate: the tool-authorization decision, as testable pure functions.

Collapses the authorization checks that were threaded procedurally through the
agent loop into one place with its own test surface — the PermissionEngine
(a shallow rule-matcher) becomes an internal detail.

Split into two pure functions ON PURPOSE, to preserve the loop's ordering
(engine policy → ASK interaction → input validation):

* ``decide``   — permission-engine policy only (allow / deny / ask).
* ``validate`` — tool self-check + input validation, run AFTER any ASK is
  approved (so a denied/ambiguous approval never silently skips validation, and
  validation never runs before the user is asked).

What stays in the loop, deliberately: the ASK interaction (emitting
AWAITING_CONFIRMATION and awaiting the async approval handler) and the tool-pool
concerns (skill allowlist, registry lookup) — those are event-stream / async,
not a pure decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..tools.base import Tool
from .permissions import PermissionBehavior, PermissionEngine

Verdict = Literal["allow", "deny", "ask"]


@dataclass
class AuthorizeDecision:
    """Engine policy outcome.

    ``message`` is the full, ready-to-surface error content for ``deny`` and the
    bare confirmation reason for ``ask``.
    """

    verdict: Verdict
    message: str = ""


class ToolGate:
    """Authorizes a single tool invocation."""

    def __init__(self, permission_engine: PermissionEngine | None = None) -> None:
        self.permission_engine = permission_engine

    def decide(self, tool: Tool, tool_input: dict[str, Any]) -> AuthorizeDecision:
        """Permission-engine policy only. No engine → allow."""
        if self.permission_engine is None:
            return AuthorizeDecision("allow")
        decision = self.permission_engine.check(tool.name, tool_input)
        if decision.behavior == PermissionBehavior.DENY:
            return AuthorizeDecision("deny", f"Permission denied: {decision.reason}")
        if decision.behavior == PermissionBehavior.ASK:
            return AuthorizeDecision("ask", decision.reason)
        return AuthorizeDecision("allow")

    def validate(self, tool: Tool, tool_input: dict[str, Any]) -> str | None:
        """Tool self-check + input validation → error message, or None if OK."""
        perm = tool.check_permissions(tool_input)
        if not perm.allowed:
            return f"Permission denied: {perm.reason}"
        validation = tool.validate_input(tool_input)
        if not validation.valid:
            return f"Validation error: {validation.error}"
        return None
