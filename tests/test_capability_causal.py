"""causal 能力域:注册、「分析/推断」边界声明、成功/失败信封(无 LLM、无网络)。"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.capabilities import CapabilityRegistry, OutputKind, Permission
from data_analysis_agent.capabilities.causal import register_all

_NAMES = ["causal_analyze", "causal_estimate", "causal_report"]


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    names = register_all(registry)
    assert names == _NAMES
    return registry


_ANALYZE_INPUT: dict[str, Any] = {
    "question": "A/B 实验,variant_b 是否提升 revenue",
    "outcome_columns": ["revenue"],
    "treatment_column": "variant",
    "control_arm": "control",
    "treatment_arms": ["variant_b"],
    "guardrail_columns": ["crash_count"],
    "business_assumptions": ["无溢出效应", "SUTVA"],
}


def _records() -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for v in [0, 1, 0, 1, 0] * 10:  # control p≈0.5
        recs.append({"variant": "control", "y": v})
    for v in [1, 1, 1, 0, 1] * 10:  # treatment p≈0.8
        recs.append({"variant": "t", "y": v})
    return recs


def _estimate_input() -> dict[str, Any]:
    return {
        "records": _records(),
        "control_group": "control",
        "treatment_groups": ["t"],
        "group_column": "variant",
        "outcome_column": "y",
        "decision_threshold": 0.0,
        "min_sample_size": 30,
    }


# ----------------------------- 注册与契约声明 -----------------------------


def test_registers_all_names_in_order():
    registry = CapabilityRegistry()
    assert register_all(registry) == _NAMES
    for name in _NAMES:
        assert registry.has(name)


def test_specs_declare_domain_permission_output_kind():
    registry = _registry()
    for name in _NAMES:
        spec = registry.get(name)
        assert spec.domain == "causal"
        assert spec.permission is Permission.READ_ONLY
        assert spec.error_codes == ("validation_error", "execution_error")
    assert registry.get("causal_analyze").output_kind is OutputKind.STRUCTURED
    assert registry.get("causal_estimate").output_kind is OutputKind.STRUCTURED
    assert registry.get("causal_report").output_kind is OutputKind.TEXT


def test_subcapability_boundary_declared_in_names_and_descriptions():
    """「分析」与「推断」子能力边界必须在契约里显式声明。"""

    registry = _registry()
    analyze = registry.get("causal_analyze")
    estimate = registry.get("causal_estimate")
    assert "分析子能力" in analyze.description
    assert "因果就绪 QA" in analyze.description
    assert "推断子能力" in estimate.description
    assert "效应估计" in estimate.description
    # 分析能力声明先于推断使用;推断能力声明承接分析产出(单向边界)
    assert "先于 causal_estimate" in analyze.description
    assert "承接 causal_analyze" in estimate.description


# ----------------------------- causal_analyze(分析) -----------------------------


async def test_analyze_experiment_ready_end_to_end():
    registry = _registry()
    env = await registry.execute("causal_analyze", _ANALYZE_INPUT)
    assert env["ok"] is True
    contract = env["data"]["causal_contract"]
    qa = env["data"]["causal_qa"]
    assert contract["claim_level"] == "experimental"
    assert contract["assignment_mechanism"] == "randomized"
    assert contract["outcome_columns"] == ["revenue"]
    assert qa["readiness"] == "experiment_ready"
    assert "readiness: experiment_ready" in env["content"]
    assert "claim_level: experimental" in env["content"]


async def test_analyze_surfaces_missing_context_without_guessing():
    registry = _registry()
    env = await registry.execute("causal_analyze", {"question": "新功能是否导致了收入变化?"})
    assert env["ok"] is True
    contract = env["data"]["causal_contract"]
    assert len(contract["missing_context"]) > 0
    assert contract["treatment_column"] is None
    assert contract["outcome_columns"] == []
    # 因果问题但缺处理/结果 → 绝不 experiment_ready
    assert env["data"]["causal_qa"]["readiness"] != "experiment_ready"


async def test_analyze_correlation_is_associational():
    registry = _registry()
    env = await registry.execute("causal_analyze", {"question": "收入与广告支出相关吗?"})
    assert env["ok"] is True
    assert env["data"]["causal_contract"]["claim_level"] == "associational"


async def test_analyze_validation_error_on_bad_question():
    registry = _registry()
    for bad in ({}, {"question": ""}, {"question": "   "}):
        env = await registry.execute("causal_analyze", bad)
        assert env["ok"] is False
        assert env["error"]["code"] == "validation_error"


# ----------------------------- causal_estimate(推断) -----------------------------


async def test_estimate_ship_path_with_action_plan():
    registry = _registry()
    env = await registry.execute("causal_estimate", _estimate_input())
    assert env["ok"] is True
    readout = env["data"]["experiment_readout"]
    plan = env["data"]["causal_action_plan"]
    assert readout["aggregate_decision"] == "ship"
    assert len(readout["contrasts"]) == 1
    assert plan["decision"] == "ship"
    assert plan["recommendations"]
    assert "aggregate_decision: ship" in env["content"]
    assert "decision: ship" in env["content"]


async def test_estimate_accepts_columns_form():
    records = _records()
    env = await _registry().execute(
        "causal_estimate",
        {
            "columns": {
                "variant": [r["variant"] for r in records],
                "y": [r["y"] for r in records],
            },
            "control_group": "control",
            "treatment_groups": ["t"],
            "group_column": "variant",
            "outcome_column": "y",
        },
    )
    assert env["ok"] is True
    assert env["data"]["experiment_readout"]["contrasts"][0]["treatment_arm"] == "t"


async def test_estimate_needs_more_data_for_missing_arm():
    env = await _registry().execute(
        "causal_estimate",
        {
            "records": [{"variant": "control", "y": 1}, {"variant": "control", "y": 0}],
            "control_group": "control",
            "treatment_groups": ["ghost"],
            "group_column": "variant",
            "outcome_column": "y",
        },
    )
    assert env["ok"] is True
    assert env["data"]["experiment_readout"]["aggregate_decision"] == "needs_more_data"
    # 行动计划仍产出,但绝不升级为 ship
    assert env["data"]["causal_action_plan"]["decision"] != "ship"


async def test_estimate_with_contract_links_assumptions():
    registry = _registry()
    analyze_env = await registry.execute("causal_analyze", _ANALYZE_INPUT)
    inputs = _estimate_input()
    inputs["question"] = _ANALYZE_INPUT["question"]
    inputs["causal_contract"] = analyze_env["data"]["causal_contract"]
    env = await registry.execute("causal_estimate", inputs)
    assert env["ok"] is True
    assert env["data"]["causal_action_plan"]["decision"] == "ship"


async def test_estimate_validation_errors():
    registry = _registry()
    base = _estimate_input()
    # 既无 records 又无 columns
    no_data = {k: v for k, v in base.items() if k != "records"}
    env = await registry.execute("causal_estimate", no_data)
    assert env["error"]["code"] == "validation_error"
    assert "records or columns" in env["error"]["message"]
    # treatment_groups 空
    env = await registry.execute("causal_estimate", {**base, "treatment_groups": []})
    assert env["error"]["code"] == "validation_error"
    # min_sample_size < 2
    env = await registry.execute("causal_estimate", {**base, "min_sample_size": 1})
    assert env["error"]["code"] == "validation_error"
    # outcome_kind=proportion 但结果列非二元
    env = await registry.execute(
        "causal_estimate",
        {
            "records": [{"v": "c", "y": 2.5}, {"v": "t", "y": 3.5}],
            "control_group": "c",
            "treatment_groups": ["t"],
            "group_column": "v",
            "outcome_column": "y",
            "outcome_kind": "proportion",
        },
    )
    assert env["error"]["code"] == "validation_error"
    # 非法 outcome_kind
    env = await registry.execute("causal_estimate", {**base, "outcome_kind": "bogus"})
    assert env["error"]["code"] == "validation_error"


async def test_estimate_execution_error_envelope():
    """内部未预期异常 → fail-closed execution_error 信封(无 traceback 泄漏)。"""

    inputs = _estimate_input()
    inputs["expected_ratio"] = ["not-a-number", "also-not"]
    env = await _registry().execute("causal_estimate", inputs)
    assert env["ok"] is False
    assert env["error"]["code"] == "execution_error"
    assert "Traceback" not in env["error"]["message"]


# ----------------------------- causal_report -----------------------------


async def _analyze_data(registry: CapabilityRegistry) -> dict[str, Any]:
    env = await registry.execute("causal_analyze", _ANALYZE_INPUT)
    assert env["ok"] is True
    return dict(env["data"])


async def test_report_builds_document_from_full_chain():
    registry = _registry()
    analyze_data = await _analyze_data(registry)
    estimate_env = await registry.execute("causal_estimate", _estimate_input())
    assert estimate_env["ok"] is True
    env = await registry.execute(
        "causal_report",
        {
            "causal_contract": analyze_data["causal_contract"],
            "causal_qa": analyze_data["causal_qa"],
            "experiment_readout": estimate_env["data"]["experiment_readout"],
            "causal_action_plan": estimate_env["data"]["causal_action_plan"],
        },
    )
    assert env["ok"] is True
    document = env["data"]["document"]
    assert document["title"]
    roles = [b["role"] for b in document["blocks"]]
    # 因果规则:每个 FINDING 紧跟一个 CAVEAT
    for idx, role in enumerate(roles):
        if role == "finding":
            assert roles[idx + 1] == "caveat"
    assert "recommendation" in roles
    assert "ReportDocument" in env["content"]


async def test_report_observational_only_synthesizes_stub():
    registry = _registry()
    env_analyze = await registry.execute("causal_analyze", {"question": "收入与广告支出相关吗?"})
    env = await registry.execute(
        "causal_report",
        {
            "causal_contract": env_analyze["data"]["causal_contract"],
            "causal_qa": env_analyze["data"]["causal_qa"],
        },
    )
    assert env["ok"] is True
    assert env["data"]["document"]["blocks"]  # 空 readout 也能产出带 FINDING/CAVEAT 的文档


async def test_report_validation_errors():
    registry = _registry()
    analyze_data = await _analyze_data(registry)
    # 缺 causal_qa
    env = await registry.execute(
        "causal_report", {"causal_contract": analyze_data["causal_contract"]}
    )
    assert env["error"]["code"] == "validation_error"
    # 缺 causal_contract
    env = await registry.execute("causal_report", {"causal_qa": analyze_data["causal_qa"]})
    assert env["error"]["code"] == "validation_error"
    # 残缺 payload(必填键缺失)→ validation_error 而非 execution_error
    env = await registry.execute(
        "causal_report", {"causal_contract": {"question": "q"}, "causal_qa": {}}
    )
    assert env["error"]["code"] == "validation_error"
