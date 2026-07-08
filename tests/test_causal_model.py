"""causal.model:构造、默认值、to_dict/from_dict 往返、frozen 不可变、可哈希。"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from data_analysis_agent.causal.model import (
    ActionPlan,
    ActionRecommendation,
    AssignmentMechanism,
    CausalContract,
    CausalFinding,
    CausalIntent,
    CausalQAReport,
    CausalQuestion,
    CausalReadiness,
    ClaimLevel,
    ContrastResult,
    DecisionLevel,
    EffectEstimate,
    ExperimentReadout,
    GuardrailResult,
    OutcomeKind,
    SegmentBreakdown,
    SourceKind,
    SRMResult,
    VariableBinding,
    VariableRole,
)

# ----------------------------- 构造与默认值 -----------------------------


def test_causal_intent_defaults():
    ci = CausalIntent()
    assert ci.has_intervention is False
    assert ci.assignment_hint is AssignmentMechanism.UNKNOWN
    assert ci.detected_outcome_terms == ()


def test_causal_contract_defaults_empty():
    c = CausalContract(question="q")
    assert c.claim_level is ClaimLevel.DESCRIPTIVE
    assert c.treatment_column is None
    assert c.outcome_columns == ()
    assert c.decision_threshold == 0.0
    assert c.min_sample_size == 30
    assert c.field_sources == ()


def test_effect_estimate_required_fields():
    e = EffectEstimate(
        outcome_column="revenue",
        outcome_kind=OutcomeKind.MEAN,
        control_n=100,
        treatment_n=120,
    )
    assert e.degenerate is False
    assert e.ci_lower is None


# ----------------------------- 往返契约 -----------------------------


@pytest.fixture
def roundtrip_objects() -> list[object]:
    return [
        CausalIntent(
            has_intervention=True,
            has_randomization_signal=True,
            wants_lift_or_effect=True,
            has_observation_marker=False,
            assignment_hint=AssignmentMechanism.RANDOMIZED,
            detected_outcome_terms=("留存", "revenue"),
            detected_treatment_terms=("variant",),
            rationale="detected: intervention,randomization",
        ),
        CausalQuestion(
            question="实验组是否提高留存?",
            intent=CausalIntent(has_randomization_signal=True),
            data_context_refs=("dc1",),
        ),
        VariableBinding(
            column="variant", role=VariableRole.TREATMENT, source=SourceKind.EXPLICIT_USER
        ),
        CausalContract(
            question="q",
            claim_level=ClaimLevel.EXPERIMENTAL,
            assignment_mechanism=AssignmentMechanism.RANDOMIZED,
            outcome_columns=("revenue", "retention_d7"),
            treatment_column="variant",
            control_arm="control",
            treatment_arms=("variant_a", "variant_b"),
            guardrail_columns=("crash_count",),
            expected_ratio=(1.0, 1.0, 1.0),
            decision_threshold=0.01,
            min_sample_size=50,
            business_assumptions=("无溢出效应",),
            external_events=("节假日",),
            variables=(
                VariableBinding(column="revenue", role=VariableRole.OUTCOME),
                VariableBinding(column="crash_count", role=VariableRole.GUARDRAIL),
            ),
            field_sources=(
                ("treatment_column", SourceKind.EXPLICIT_USER),
                ("outcome_columns", SourceKind.IMPLICIT_USER),
            ),
            missing_context=("time_window",),
            intent=CausalIntent(has_randomization_signal=True),
        ),
        CausalFinding(
            severity="high",
            code="causal.needs_assumptions",
            message="缺假设",
            suggested_fix="补充可忽略性假设",
        ),
        CausalQAReport(
            readiness=CausalReadiness.EXPERIMENT_READY,
            findings=(CausalFinding(severity="info", code="x", message="m"),),
            contract_exists=True,
        ),
        EffectEstimate(
            outcome_column="revenue",
            outcome_kind=OutcomeKind.MEAN,
            control_n=255,
            treatment_n=227,
            control_mean=3.5,
            treatment_mean=4.1,
            effect=0.6,
            relative_effect=0.171,
            se=0.2,
            ci_lower=0.208,
            ci_upper=0.992,
            z=3.0,
            p_value=0.0027,
            significant=True,
            notes=("welch_z_approx",),
        ),
        EffectEstimate(
            outcome_column="retention_d7",
            outcome_kind=OutcomeKind.MEAN,
            control_n=0,
            treatment_n=0,
            degenerate=True,
            notes=("empty_group",),
        ),
        SRMResult(
            arms=("control", "variant_a", "variant_b"),
            observed=(255, 218, 227),
            expected=(233.33, 233.33, 233.33),
            chi_square=2.057,
            df=2,
            critical_value=5.991,
            srm_detected=False,
        ),
        GuardrailResult(
            column="crash_count",
            estimate=EffectEstimate("crash_count", OutcomeKind.MEAN, 255, 227),
            unfavorable_direction="higher_is_worse",
            tolerance=0.0,
            breached=False,
        ),
        SegmentBreakdown(
            column="country", note="US", arm_sizes=(("control", 30), ("variant_b", 28))
        ),
        ContrastResult(
            treatment_arm="variant_b",
            outcome_estimate=EffectEstimate(
                "revenue",
                OutcomeKind.MEAN,
                255,
                227,
                effect=0.6,
                ci_lower=0.2,
                ci_upper=1.0,
                significant=True,
            ),
            guardrails=(
                GuardrailResult(
                    column="crash_count",
                    estimate=EffectEstimate("crash_count", OutcomeKind.MEAN, 255, 227),
                    unfavorable_direction="higher_is_worse",
                ),
            ),
            decision=DecisionLevel.SHIP,
            decision_reasons=("significant_positive_and_threshold_met",),
        ),
        ExperimentReadout(
            contract_question="q",
            control_arm="control",
            outcome_column="revenue",
            outcome_kind=OutcomeKind.MEAN,
            contrasts=(
                ContrastResult(
                    treatment_arm="variant_b",
                    outcome_estimate=EffectEstimate("revenue", OutcomeKind.MEAN, 255, 227),
                ),
            ),
            srm=SRMResult(
                arms=("control", "variant_b"), observed=(255, 227), expected=(241.0, 241.0)
            ),
            aggregate_decision=DecisionLevel.INCONCLUSIVE,
            aggregate_reasons=("srm_contamination",),
            total_n=482,
            notes=("welch_z_approx",),
        ),
        ActionRecommendation(
            code="hold", target_arm="variant_b", rationale="SRM", precondition="排查分流日志"
        ),
        ActionPlan(
            decision=DecisionLevel.INCONCLUSIVE,
            recommendations=(ActionRecommendation(code="fix_srm"),),
            assumptions=("无溢出效应",),
            refutations=("安慰剂检验",),
            open_risks=("分流异常未排除",),
        ),
    ]


def test_roundtrip(roundtrip_objects: list[object]) -> None:
    for obj in roundtrip_objects:
        rebuilt = type(obj).from_dict(obj.to_dict())  # type: ignore[attr-defined]
        assert rebuilt == obj, f"round-trip mismatch for {type(obj).__name__}"


def test_enum_serializes_to_value():
    c = CausalContract(
        question="q",
        claim_level=ClaimLevel.EXPERIMENTAL,
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
    )
    payload = c.to_dict()
    assert payload["claim_level"] == "experimental"
    assert payload["assignment_mechanism"] == "randomized"
    assert isinstance(payload["claim_level"], str)


def test_field_sources_roundtrip_preserves_enum():
    c = CausalContract(
        question="q",
        field_sources=(
            ("treatment_column", SourceKind.EXPLICIT_USER),
            ("outcome", SourceKind.IMPLICIT_USER),
        ),
    )
    rebuilt = CausalContract.from_dict(c.to_dict())
    assert rebuilt.field_sources == (
        ("treatment_column", SourceKind.EXPLICIT_USER),
        ("outcome", SourceKind.IMPLICIT_USER),
    )


def test_to_dict_is_json_serializable(roundtrip_objects: list[object]) -> None:
    for obj in roundtrip_objects:
        json.dumps(obj.to_dict())  # type: ignore[attr-defined]


def test_from_dict_ignores_unknown_keys():
    c = CausalContract(question="q")
    payload = c.to_dict()
    payload["__unknown__"] = "ignored"
    assert CausalContract.from_dict(payload) == c


def test_nested_intent_default_when_absent():
    """from_dict 缺 intent 键时回退到默认 CausalIntent(Serializable 重建嵌套)。"""
    c = CausalContract(question="q")
    payload = c.to_dict()
    rebuilt = CausalContract.from_dict(payload)
    assert rebuilt.intent == CausalIntent()


# ----------------------------- frozen 不可变 -----------------------------


def test_frozen_causal_contract():
    c = CausalContract(question="q")
    with pytest.raises(FrozenInstanceError):
        c.question = "other"  # type: ignore[misc]
    assert replace(c, question="z").question == "z"


def test_hashable():
    assert hash(CausalIntent()) is not None
    assert hash(CausalContract(question="q")) is not None
    assert hash(EffectEstimate("r", OutcomeKind.MEAN, 1, 1)) is not None
