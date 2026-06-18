"""Tests for the tool-authorization seam (ToolGate).

ToolGate is the test surface for what used to be procedural authorization
threaded through the agent loop. The two functions are split on purpose to
preserve the loop's ordering — these tests pin that contract:

* ``decide``   — permission-engine policy only (allow / deny / ask).
* ``validate`` — tool self-check THEN input validation, run after any ASK.
"""

from typing import Any

from data_analysis_agent.security.permissions import (
    PermissionBehavior,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from data_analysis_agent.security.tool_gate import AuthorizeDecision, ToolGate
from data_analysis_agent.tools.base import PermissionResult, Tool, ToolResult, ValidationResult


class _StubTool(Tool):
    """Minimal tool whose self-check / validation outcomes are injectable."""

    def __init__(
        self,
        name: str = "stub",
        *,
        permission: PermissionResult | None = None,
        validation: ValidationResult | None = None,
    ) -> None:
        self._name = name
        self._permission = permission or PermissionResult.allow()
        self._validation = validation or ValidationResult.success()

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "stub"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    def check_permissions(self, input_data: dict[str, Any]) -> PermissionResult:
        return self._permission

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        return self._validation

    async def call(self, input_data: dict[str, Any], can_use_tool: Any = None) -> ToolResult:
        return ToolResult(content="ok")


# --- decide(): permission-engine policy only --------------------------------


def test_decide_allows_when_no_engine():
    gate = ToolGate(None)
    decision = gate.decide(_StubTool(), {})
    assert decision == AuthorizeDecision("allow")


def test_decide_allow_passes_through():
    engine = PermissionEngine()
    engine.add_rule(PermissionRule("stub", PermissionBehavior.ALLOW))
    decision = ToolGate(engine).decide(_StubTool(), {})
    assert decision.verdict == "allow"


def test_decide_deny_wraps_reason_into_ready_message():
    engine = PermissionEngine()
    engine.add_rule(PermissionRule("stub", PermissionBehavior.DENY))
    decision = ToolGate(engine).decide(_StubTool(), {})
    assert decision.verdict == "deny"
    # Same wording the loop used to emit inline.
    assert decision.message.startswith("Permission denied:")


def test_decide_ask_keeps_bare_reason():
    engine = PermissionEngine()
    engine.add_rule(PermissionRule("stub", PermissionBehavior.ASK))
    decision = ToolGate(engine).decide(_StubTool(), {})
    assert decision.verdict == "ask"
    # ASK message is the engine's bare reason — the loop frames the prompt around it.
    assert decision.message == "Matched ask rule: stub"


def test_decide_default_mode_unmatched_tool_asks():
    # DEFAULT mode with no matching allow rule falls through to ASK.
    engine = PermissionEngine(mode=PermissionMode.DEFAULT)
    decision = ToolGate(engine).decide(_StubTool("unknown"), {})
    assert decision.verdict == "ask"


# --- validate(): tool self-check THEN input validation ----------------------


def test_validate_returns_none_when_clean():
    gate = ToolGate(None)
    assert gate.validate(_StubTool(), {}) is None


def test_validate_surfaces_permission_failure():
    tool = _StubTool(permission=PermissionResult.deny("path escapes sandbox"))
    msg = ToolGate(None).validate(tool, {})
    assert msg == "Permission denied: path escapes sandbox"


def test_validate_surfaces_validation_failure():
    tool = _StubTool(validation=ValidationResult.fail("missing 'code'"))
    msg = ToolGate(None).validate(tool, {})
    assert msg == "Validation error: missing 'code'"


def test_validate_checks_permission_before_validation():
    """Ordering invariant: a permission failure short-circuits before input
    validation — preserving the loop's original check_permissions→validate_input
    order even when BOTH would fail."""
    tool = _StubTool(
        permission=PermissionResult.deny("denied first"),
        validation=ValidationResult.fail("would also fail"),
    )
    msg = ToolGate(None).validate(tool, {})
    assert msg == "Permission denied: denied first"  # not the validation error


def test_decide_and_validate_are_independent():
    """decide() never runs tool self-check/validation; validate() never consults
    the engine — the split is what lets the loop interleave the ASK between them."""
    engine = PermissionEngine()
    engine.add_rule(PermissionRule("stub", PermissionBehavior.ASK))
    tool = _StubTool(validation=ValidationResult.fail("bad input"))
    gate = ToolGate(engine)
    # decide() ignores the broken validation entirely.
    assert gate.decide(tool, {}).verdict == "ask"
    # validate() ignores the engine entirely.
    assert gate.validate(tool, {}) == "Validation error: bad input"
