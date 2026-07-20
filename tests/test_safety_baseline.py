"""Tests for the safety baseline (P1-1, slice 2a): permission presets + sensitive mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_analysis_agent.config import AgentConfig
from data_analysis_agent.runtime import build_permission_engine
from data_analysis_agent.security.permissions import (
    PermissionBehavior,
    PermissionEngine,
    PermissionRule,
)

# --- PermissionEngine.default_behavior ---


def test_engine_default_is_ask_for_unknown_tool() -> None:
    """Original posture preserved: unknown tool → ASK when default_behavior unset."""
    eng = PermissionEngine()
    assert eng.check("unknown_tool", {}).behavior == PermissionBehavior.ASK


def test_engine_default_deny_blocks_unknown_tool() -> None:
    eng = PermissionEngine(default_behavior=PermissionBehavior.DENY)
    assert eng.check("unknown_tool", {}).behavior == PermissionBehavior.DENY


def test_engine_allow_rule_still_wins_over_default_deny() -> None:
    """A read-only ALLOW rule must beat a deny-by-default fall-through."""
    eng = PermissionEngine(default_behavior=PermissionBehavior.DENY)
    eng.add_rule(PermissionRule("read_file", PermissionBehavior.ALLOW))
    assert eng.check("read_file", {}).behavior == PermissionBehavior.ALLOW
    assert eng.check("other", {}).behavior == PermissionBehavior.DENY


# --- build_permission_engine presets ---


def test_local_safe_preset_allows_readonly_asks_mutators_denies_unknown() -> None:
    eng = build_permission_engine(AgentConfig(permission_preset="local_safe"))
    assert eng is not None
    for ro in ("read_file", "data_profile", "report_contract", "retrieve_result"):
        assert eng.check(ro, {}).behavior == PermissionBehavior.ALLOW, ro
    for mutator in ("python_analysis", "visualization", "html_report", "chart_render"):
        assert eng.check(mutator, {}).behavior == PermissionBehavior.ASK, mutator
    assert eng.check("never_registered", {}).behavior == PermissionBehavior.DENY


def test_local_dev_preset_returns_no_engine() -> None:
    assert build_permission_engine(AgentConfig(permission_preset="local_dev")) is None


def test_no_preset_default_no_deny_returns_no_engine() -> None:
    """Pre-slice behaviour unchanged: bare default config → no engine."""
    assert build_permission_engine(AgentConfig()) is None


# --- sensitive_mode suppresses capture ---


def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "daa_home"
    home.mkdir()
    monkeypatch.setenv("DAA_HOME", str(home))
    return home


def test_sensitive_mode_suppresses_memory_and_trajectory_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from data_analysis_agent.runtime import AgentRuntime

    _isolated_home(tmp_path, monkeypatch)
    config = AgentConfig(api_key="k", sensitive_mode=True, persistent_kernel=False)
    runtime = AgentRuntime.from_config(config)

    # Memory writes suppressed (enable_memory forced False).
    assert runtime.memory_injector is None
    # Trajectory input capture suppressed; telemetry still on but input-less.
    logger = runtime.session.trajectory_logger
    assert logger is not None
    assert logger._enable_inputs is False


def test_non_sensitive_keeps_memory_and_trajectory_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from data_analysis_agent.runtime import AgentRuntime

    _isolated_home(tmp_path, monkeypatch)
    config = AgentConfig(api_key="k", persistent_kernel=False)
    runtime = AgentRuntime.from_config(config)

    assert runtime.memory_injector is not None
    assert runtime.session.trajectory_logger._enable_inputs is True


# --- local_safe classification guard (M1: prevent silent DENY of a built-in) ---


def test_every_builtin_tool_is_classified_for_local_safe() -> None:
    """A future tool added without classification would be silently DENY'd."""
    from data_analysis_agent.runtime import (
        MUTATOR_TOOLS,
        READ_ONLY_TOOLS,
        build_registry,
    )

    registry = build_registry(AgentConfig())
    names = {tool.name for tool in registry.get_all_base_tools()}
    classified = set(READ_ONLY_TOOLS) | set(MUTATOR_TOOLS)
    unclassified = names - classified
    assert not unclassified, (
        f"built-in tools not classified for local_safe (would be DENY'd): {unclassified}"
    )


# --- sensitive-mode must not leak the raw query to disk (B1/B2/B3) ---


def test_trajectory_blanks_user_input_when_inputs_disabled(tmp_path: Path) -> None:
    """enable_inputs=False (forced by sensitive mode) blanks the persisted query."""
    import json

    from data_analysis_agent.events import (
        CompleteEvent,
        StreamTextEvent,
        ToolResultEvent,
        ToolUseEvent,
    )
    from data_analysis_agent.telemetry.trajectory import TrajectoryLogger

    logger = TrajectoryLogger(tmp_path / "traj", "s1", enable_inputs=False)
    logger.begin_turn("MY SECRET SSN 123-45-6789")
    logger(ToolUseEvent(tool_use_id="t1", tool_name="data_profile", parameters={"path": "/x.csv"}))
    logger(ToolResultEvent(tool_use_id="t1", tool_name="data_profile", content="ok"))
    logger(StreamTextEvent(text="SECRET MODEL OUTPUT"))
    logger(CompleteEvent(terminal_reason="done", final_text="SECRET MODEL OUTPUT"))
    logger.end_turn()

    traj_file = tmp_path / "traj" / "s1.jsonl"
    assert traj_file.is_file()
    raw = traj_file.read_text(encoding="utf-8")
    record = json.loads(raw.splitlines()[0])
    assert record["user_input"] == ""
    assert record["final_text_digest"] == ""
    # The concrete guarantee: the secret never reaches the file.
    assert "SECRET" not in raw


def test_sensitive_run_redacts_manifest_request_and_skips_session_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under --sensitive --project: no session file, and the manifest request is redacted."""
    import json

    from data_analysis_agent.__main__ import _record_run
    from data_analysis_agent.runtime import AgentRuntime
    from data_analysis_agent.workspace import Project

    home = _isolated_home(tmp_path, monkeypatch)
    proj = Project.init("demo", home=home)
    config = AgentConfig(
        api_key="k",
        sensitive_mode=True,
        persistent_kernel=False,
        enable_telemetry=False,
        enable_memory=False,
    )
    runtime = AgentRuntime.from_config(config, project=proj)

    assert runtime.sensitive_mode is True
    # B3: no message store → no session jsonl path is created.
    assert runtime.session.store is None
    assert not proj.session_path(runtime.run_id).exists()

    # B2: the manifest redacts the user's query.
    stats = {
        "event_counts": {"CompleteEvent": 1},
        "tool_calls": {},
        "artifacts": [],
        "terminal_reason": "done",
        "token_usage": {"input_tokens": 0, "output_tokens": 0},
    }
    manifest_path = _record_run(runtime, "MY SECRET QUERY", ["/data/x.csv"], stats, "t0", "t1")
    assert manifest_path is not None
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert record["request"] == "<redacted: sensitive-mode>"
    assert "SECRET" not in manifest_path.read_text(encoding="utf-8")
