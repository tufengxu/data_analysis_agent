"""causal.qa:确定性因果就绪分类(6 态)+ 闭词汇 finding。"""

from __future__ import annotations

from data_analysis_agent.causal.model import (
    AssignmentMechanism,
    CausalContract,
    CausalIntent,
    CausalReadiness,
    ClaimLevel,
)
from data_analysis_agent.causal.qa import run_causal_qa


def _contract(**kwargs) -> CausalContract:
    return CausalContract(question="q", **kwargs)


def _finding_codes(report) -> set[str]:
    return {f.code for f in report.findings}


# ----------------------------- 6 态分类 -----------------------------


def test_not_causal_for_descriptive_question():
    c = _contract()  # 默认 claim_level DESCRIPTIVE,无因果 intent
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.NOT_CAUSAL
    assert "causal.not_causal" in _finding_codes(report)


def test_correlation_only_is_not_causal_not_experiment_ready():
    # 纯相关(观察性表述,无干预/随机化)→ NOT_CAUSAL,绝不到 EXPERIMENT_READY
    c = _contract(
        claim_level=ClaimLevel.ASSOCIATIONAL,
        intent=CausalIntent(has_observation_marker=True),
    )
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.NOT_CAUSAL
    assert report.readiness is not CausalReadiness.EXPERIMENT_READY


def test_blocked_when_causal_claim_but_assignment_unknown():
    c = _contract(
        claim_level=ClaimLevel.ASSOCIATIONAL,
        intent=CausalIntent(has_intervention=True),
        assignment_mechanism=AssignmentMechanism.UNKNOWN,
    )
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.BLOCKED
    assert "causal.assignment_unknown" in _finding_codes(report)


def test_needs_assumptions_when_randomized_but_no_assumptions():
    c = _contract(
        claim_level=ClaimLevel.EXPERIMENTAL,
        intent=CausalIntent(has_randomization_signal=True),
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
        treatment_column="variant",
        control_arm="control",
        treatment_arms=("variant_b",),
        outcome_columns=("revenue",),
    )
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.NEEDS_ASSUMPTIONS
    assert "causal.needs_assumptions" in _finding_codes(report)


def test_experiment_ready_when_randomized_assumptions_and_fields():
    c = _contract(
        claim_level=ClaimLevel.EXPERIMENTAL,
        intent=CausalIntent(has_randomization_signal=True),
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
        treatment_column="variant",
        control_arm="control",
        treatment_arms=("variant_b",),
        outcome_columns=("revenue",),
        guardrail_columns=("crash_count",),
        business_assumptions=("无溢出效应", "SUTVA 成立"),
    )
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.EXPERIMENT_READY
    # 无 blocker/high
    severities = {f.severity for f in report.findings}
    assert "blocker" not in severities
    assert "high" not in severities


def test_needs_data_when_assumptions_present_but_outcome_missing():
    c = _contract(
        claim_level=ClaimLevel.EXPERIMENTAL,
        intent=CausalIntent(has_randomization_signal=True),
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
        treatment_column="variant",
        control_arm="control",
        treatment_arms=("variant_b",),
        outcome_columns=(),  # 缺结果
        business_assumptions=("无溢出效应",),
    )
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.NEEDS_DATA
    assert "causal.needs_data" in _finding_codes(report)


def test_assumption_ready_for_observational_with_assumptions():
    c = _contract(
        claim_level=ClaimLevel.CAUSAL_ASSUMPTION,
        intent=CausalIntent(has_intervention=True),
        assignment_mechanism=AssignmentMechanism.SELF_SELECTION,
        treatment_column="campaign",
        outcome_columns=("revenue",),
        business_assumptions=("可忽略性:无未观测混淆",),
    )
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.ASSUMPTION_READY
    assert report.readiness is not CausalReadiness.EXPERIMENT_READY
    assert "causal.observational_assumption" in _finding_codes(report)


# ----------------------------- 不变量:观察性永不 EXPERIMENT_READY -----------------------------


def test_observational_never_reaches_experiment_ready():
    c = _contract(
        claim_level=ClaimLevel.CAUSAL_ASSUMPTION,
        intent=CausalIntent(has_intervention=True),
        assignment_mechanism=AssignmentMechanism.SELF_SELECTION,
        treatment_column="campaign",
        control_arm="no_campaign",
        treatment_arms=("campaign",),
        outcome_columns=("revenue",),
        guardrail_columns=("crash_count",),
        business_assumptions=("可忽略性", "无溢出"),
    )
    report = run_causal_qa(c)
    # 观察性即便字段齐全,也只能 ASSUMPTION_READY,不可 EXPERIMENT_READY
    assert report.readiness is CausalReadiness.ASSUMPTION_READY


def test_authoritative_mechanism_beats_randomization_text_signal():
    # 回归(独立审查 MUST-FIX):问句含"实验组"(intent 随机化信号),但权威机制是
    # self_selection → 不得 EXPERIMENT_READY(只能 ASSUMPTION_READY)。
    c = _contract(
        claim_level=ClaimLevel.CAUSAL_ASSUMPTION,
        intent=CausalIntent(has_randomization_signal=True, has_intervention=True),
        assignment_mechanism=AssignmentMechanism.SELF_SELECTION,
        treatment_column="campaign",
        outcome_columns=("revenue",),
        business_assumptions=("可忽略性",),
    )
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.ASSUMPTION_READY
    assert report.readiness is not CausalReadiness.EXPERIMENT_READY


# ----------------------------- 附加检查(不改 readiness) -----------------------------


def test_no_guardrail_finding_when_missing():
    c = _contract(
        claim_level=ClaimLevel.EXPERIMENTAL,
        intent=CausalIntent(has_randomization_signal=True),
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
        treatment_column="variant",
        control_arm="control",
        treatment_arms=("variant_b",),
        outcome_columns=("revenue",),
        business_assumptions=("无溢出效应",),
    )
    report = run_causal_qa(c)
    assert "causal.no_guardrail" in _finding_codes(report)


def test_multiple_comparison_finding_for_multi_arm():
    c = _contract(
        claim_level=ClaimLevel.EXPERIMENTAL,
        intent=CausalIntent(has_randomization_signal=True),
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
        treatment_column="variant",
        control_arm="control",
        treatment_arms=("variant_a", "variant_b"),
        outcome_columns=("revenue",),
        guardrail_columns=("crash_count",),
        business_assumptions=("无溢出效应",),
    )
    report = run_causal_qa(c)
    assert report.readiness is CausalReadiness.EXPERIMENT_READY
    assert "stats.no_multiple_comparison_correction" in _finding_codes(report)


def test_spillover_finding_when_assumptions_silent():
    c = _contract(
        claim_level=ClaimLevel.EXPERIMENTAL,
        intent=CausalIntent(has_randomization_signal=True),
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
        treatment_column="variant",
        control_arm="control",
        treatment_arms=("variant_b",),
        outcome_columns=("revenue",),
        guardrail_columns=("crash_count",),
        business_assumptions=("可忽略性假设",),  # 未提溢出/SUTVA
    )
    report = run_causal_qa(c)
    assert "causal.spillover_unchecked" in _finding_codes(report)


def test_deterministic_same_input_same_output():
    c = _contract(
        claim_level=ClaimLevel.EXPERIMENTAL,
        intent=CausalIntent(has_randomization_signal=True),
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
        treatment_column="variant",
        control_arm="control",
        treatment_arms=("variant_b",),
        outcome_columns=("revenue",),
        business_assumptions=("无溢出效应",),
    )
    r1 = run_causal_qa(c)
    r2 = run_causal_qa(c)
    assert r1 == r2  # 确定性:同输入同输出
