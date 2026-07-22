"""Tests for the composition root — one assembly path for CLI and eval.

The headline guarantee: the eval-flavored runtime gets the SAME tool set as
production (no more "eval ran a lighter 2-tool agent" drift), differing only by
the config feature switches.
"""

from dataclasses import replace

from data_analysis_agent.config import AgentConfig
from data_analysis_agent.runtime import AgentRuntime, build_registry, build_skill_registry
from data_analysis_agent.skills.loader import DeclarativeSkill

_PROD_TOOLS = {
    "read_file",
    "data_profile",
    "data_quality",
    "join_planner",
    "metric_contract",
    "python_analysis",
    "nl_query",
    "visualization",
    "retrieve_result",
    "html_report",
    "report_need",
    "report_context",
    "report_contract",
    "causal_contract",
    "causal_qa",
    "experiment_readout",
    "causal_action_plan",
    "causal_report",
    "chart_render",
}


class _FakeClient:
    model = "dummy"


def _eval_config() -> AgentConfig:
    return replace(
        AgentConfig(), persistent_kernel=False, enable_memory=False, enable_telemetry=False
    )


def test_eval_runtime_has_full_production_tool_set():
    runtime = AgentRuntime.from_config(_eval_config(), client=_FakeClient())
    assert set(runtime.loop.registry.list_tools()) == _PROD_TOOLS  # not a lighter subset


def test_eval_switches_disable_kernel_and_telemetry():
    runtime = AgentRuntime.from_config(_eval_config(), client=_FakeClient())
    assert runtime.kernel is None  # persistent_kernel off
    assert runtime.session.trajectory_logger is None  # telemetry off
    assert runtime.loop.memory_injector is None  # memory off


def test_extra_skills_are_registered():
    skill = DeclarativeSkill(
        name="cohort_analysis", description="d", instructions="i", keywords=["留存"]
    )
    runtime = AgentRuntime.from_config(_eval_config(), client=_FakeClient(), extra_skills=[skill])
    assert runtime.loop.skill_registry is not None
    assert runtime.loop.skill_registry.get("cohort_analysis") is not None
    # Built-ins still present.
    assert runtime.loop.skill_registry.get("descriptive_analysis") is not None


def test_client_override_is_used():
    client = _FakeClient()
    runtime = AgentRuntime.from_config(_eval_config(), client=client)
    assert runtime.loop.client is client


def test_analysis_paths_reach_python_tool(tmp_path):
    runtime = AgentRuntime.from_config(
        _eval_config(), client=_FakeClient(), analysis_paths=[tmp_path]
    )
    py_tool = runtime.loop.registry.get_tool("python_analysis")
    assert py_tool is not None
    assert tmp_path.resolve() in py_tool.allowed_paths


def test_analysis_paths_reach_data_profile_tool(tmp_path):
    runtime = AgentRuntime.from_config(
        _eval_config(), client=_FakeClient(), analysis_paths=[tmp_path]
    )
    profile_tool = runtime.loop.registry.get_tool("data_profile")
    assert profile_tool is not None
    assert tmp_path.resolve() in profile_tool.allowed_paths


def test_data_profile_auto_allowed_in_plan_mode():
    from data_analysis_agent.runtime import build_permission_engine
    from data_analysis_agent.security.permissions import PermissionBehavior

    engine = build_permission_engine(replace(AgentConfig(), permission_mode="plan"))
    assert engine is not None
    # read-only discovery is allowed even in plan mode (like read_file)
    assert engine.check("data_profile", {}).behavior == PermissionBehavior.ALLOW
    # write/execute tools stay denied in plan mode
    assert engine.check("python_analysis", {}).behavior == PermissionBehavior.DENY


def test_build_registry_full_set_standalone():
    # The shared builder produces the full set even without config.
    assert set(build_registry().list_tools()) == _PROD_TOOLS


def test_build_skill_registry_extra_skills():
    skill = DeclarativeSkill(name="x", description="d", instructions="i", keywords=["foo"])
    reg = build_skill_registry(extra_skills=[skill])
    assert reg.get("x") is not None
    assert reg.get("descriptive_analysis") is not None
