"""Tests for the permission engine."""

from data_analysis_agent.security.permissions import (
    PermissionBehavior,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)


def test_deny_rule_priority():
    """Deny rules always take precedence."""
    engine = PermissionEngine()
    engine.add_rule(PermissionRule("*", PermissionBehavior.DENY))
    engine.add_rule(PermissionRule("read_file", PermissionBehavior.ALLOW))

    result = engine.check("read_file", {})
    assert result.behavior == PermissionBehavior.DENY


def test_allow_rule():
    """Allow rules grant permission when no deny matches."""
    engine = PermissionEngine()
    engine.add_rule(PermissionRule("read_*", PermissionBehavior.ALLOW))

    result = engine.check("read_file", {})
    assert result.behavior == PermissionBehavior.ALLOW


def test_ask_rule():
    """Ask rules require confirmation."""
    engine = PermissionEngine()
    engine.add_rule(PermissionRule("python_*", PermissionBehavior.ASK))

    result = engine.check("python_analysis", {})
    assert result.behavior == PermissionBehavior.ASK


def test_default_ask():
    """Default behavior is to ask when no rules match."""
    engine = PermissionEngine()
    result = engine.check("unknown_tool", {})
    assert result.behavior == PermissionBehavior.ASK


def test_bypass_mode():
    """Bypass mode auto-allows everything."""
    engine = PermissionEngine(mode=PermissionMode.BYPASS)
    engine.add_rule(PermissionRule("*", PermissionBehavior.DENY))

    result = engine.check("any_tool", {})
    assert result.behavior == PermissionBehavior.ALLOW


def test_glob_matching():
    """Glob patterns work correctly."""
    engine = PermissionEngine()
    engine.add_rule(PermissionRule("read_*", PermissionBehavior.ALLOW))
    engine.add_rule(PermissionRule("write_*", PermissionBehavior.DENY))

    assert engine.check("read_file", {}).behavior == PermissionBehavior.ALLOW
    assert engine.check("write_file", {}).behavior == PermissionBehavior.DENY
    assert engine.check("delete_file", {}).behavior == PermissionBehavior.ASK
