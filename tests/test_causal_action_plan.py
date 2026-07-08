"""causal_action_plan:build_action_plan 决策映射 + 工具封装。

断结构(ADR 0005):不断具体数值,断 decision 与 recommendation code 集合。
"""

from __future__ import annotations

from data_analysis_agent.causal.experiment import build_action_plan, compute_readout
from data_analysis_agent.causal.model import CausalContract, DecisionLevel
from data_analysis_agent.tools.causal_action_plan import CausalActionPlanTool
from data_analysis_agent.tools.experiment_readout import ExperimentReadoutTool

_PLAN_CODES = {"ship", "hold", "fix_srm", "add_power", "drop_arm", "investigate_guardrail"}


def _codes(plan) -> set[str]:
    return {r.code for r in plan.recommendations}


# ----------------------------- build_action_plan 决策映射 -----------------------------


def test_ship_decision_yields_ship_recommendation():
    group = ["c"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 0, 1] * 10  # 0.4 vs 0.8 → 正向显著
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("t",),
        group_column="v",
        outcome_column="y",
        columns={"v": group, "y": outcome},
        min_sample_size=30,
    )
    plan = build_action_plan(ro)
    assert plan.decision is DecisionLevel.SHIP
    assert "ship" in _codes(plan)
    assert _codes(plan) <= _PLAN_CODES


def test_guardrail_breach_yields_do_not_ship_and_investigate():
    group = ["c"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 0, 1] * 10  # 正向
    crash = [0] * 50 + [1] * 50  # t 崩溃率显著升高 → 护栏破阈
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("t",),
        group_column="v",
        outcome_column="y",
        columns={"v": group, "y": outcome, "crash": crash},
        guardrail_columns=("crash",),
        min_sample_size=30,
    )
    plan = build_action_plan(ro)
    assert plan.decision is DecisionLevel.DO_NOT_SHIP
    assert "hold" in _codes(plan)
    assert "investigate_guardrail" in _codes(plan)


def test_multiple_guardrails_same_arm_each_survive_dedup():
    # 同一臂上两个护栏都破阈 → 两条 investigate_guardrail(rationale 不同)都应保留
    group = ["c"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 0, 1] * 10
    crash = [0] * 50 + [1] * 50
    latency = [0] * 50 + [1] * 50
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("t",),
        group_column="v",
        outcome_column="y",
        columns={"v": group, "y": outcome, "crash": crash, "latency": latency},
        guardrail_columns=("crash", "latency"),
        min_sample_size=30,
    )
    plan = build_action_plan(ro)
    inv = [
        r for r in plan.recommendations if r.code == "investigate_guardrail" and r.target_arm == "t"
    ]
    assert len(inv) == 2  # 两个不同护栏,rationale 不同 → 均保留(去重 key 含 rationale)
    rationales = {r.rationale for r in inv}
    assert any("crash" in x for x in rationales)
    assert any("latency" in x for x in rationales)


def test_srm_yields_inconclusive_hold_and_fix_srm():
    # 200 vs 50:严重失衡 → SRM → INCONCLUSIVE(srm_contamination)
    group = ["c"] * 200 + ["t"] * 50
    outcome = [0, 1] * 100 + [1] * 50
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("t",),
        group_column="v",
        outcome_column="y",
        columns={"v": group, "y": outcome},
        min_sample_size=30,
    )
    plan = build_action_plan(ro)
    assert plan.decision is DecisionLevel.INCONCLUSIVE
    assert "hold" in _codes(plan)
    assert "fix_srm" in _codes(plan)
    assert any("SRM" in r for r in plan.open_risks)


def test_missing_arm_yields_drop_arm():
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("ghost",),
        group_column="v",
        outcome_column="y",
        columns={"v": ["c", "c"], "y": [0.0, 1.0]},
    )
    plan = build_action_plan(ro)
    assert plan.decision is DecisionLevel.NEEDS_MORE_DATA
    assert "drop_arm" in _codes(plan)


def test_low_sample_yields_add_power():
    group = ["c"] * 5 + ["t"] * 5
    outcome = [0, 1, 0, 1, 0] + [1, 1, 1, 0, 1]
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("t",),
        group_column="v",
        outcome_column="y",
        columns={"v": group, "y": outcome},
        min_sample_size=30,
    )
    plan = build_action_plan(ro)
    assert plan.decision is DecisionLevel.NEEDS_MORE_DATA
    assert "add_power" in _codes(plan)


def test_assumptions_carried_from_contract():
    group = ["c"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 0, 1] * 10
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("t",),
        group_column="v",
        outcome_column="y",
        columns={"v": group, "y": outcome},
        min_sample_size=30,
    )
    contract = CausalContract(question="q", business_assumptions=("无溢出效应", "SUTVA"))
    plan = build_action_plan(ro, contract)
    assert plan.assumptions == ("无溢出效应", "SUTVA")
    assert len(plan.refutations) > 0  # 提供反驳清单


def test_build_action_plan_deterministic():
    group = ["c"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 0, 1] * 10
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("t",),
        group_column="v",
        outcome_column="y",
        columns={"v": group, "y": outcome},
        min_sample_size=30,
    )
    assert build_action_plan(ro) == build_action_plan(ro)


# ----------------------------- 工具封装 -----------------------------


def test_tool_rejects_non_dict_readout():
    assert CausalActionPlanTool().validate_input({"experiment_readout": "x"}).valid is False
    assert CausalActionPlanTool().validate_input({}).valid is False


async def test_tool_end_to_end_from_readout():
    group = ["c"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 0, 1] * 10
    ro_result = await ExperimentReadoutTool().call(
        {
            "columns": {"v": group, "y": outcome},
            "control_group": "c",
            "treatment_groups": ["t"],
            "group_column": "v",
            "outcome_column": "y",
        }
    )
    plan_result = await CausalActionPlanTool().call(
        {"experiment_readout": ro_result.metadata["experiment_readout"]}
    )
    assert plan_result.is_error is False
    plan = plan_result.metadata["causal_action_plan"]
    assert plan["decision"] == DecisionLevel.SHIP.value
