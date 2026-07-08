"""causal.intent:确定性意图解析 + claim_level 推断。"""

from __future__ import annotations

from data_analysis_agent.causal.intent import infer_claim_level, parse_causal_intent
from data_analysis_agent.causal.model import AssignmentMechanism, ClaimLevel

# ----------------------------- 信号检测 -----------------------------


def test_randomization_signal_detected():
    ci = parse_causal_intent("我们做了一个 A/B 测试,实验组 variant_b,对照组 control")
    assert ci.has_randomization_signal is True
    assert ci.assignment_hint is AssignmentMechanism.RANDOMIZED


def test_intervention_detected():
    ci = parse_causal_intent("新版功能是否导致了收入变化?")
    assert ci.has_intervention is True


def test_lift_and_outcome_terms():
    ci = parse_causal_intent("variant_b 是否提高了 D7 留存?")
    assert ci.wants_lift_or_effect is True
    assert "留存" in ci.detected_outcome_terms
    assert "d7" in ci.detected_outcome_terms
    assert "variant" in ci.detected_treatment_terms


def test_observation_marker_detected():
    ci = parse_causal_intent("收入和广告支出高度相关")
    assert ci.has_observation_marker is True
    assert ci.has_randomization_signal is False


def test_no_signal_descriptive_question():
    ci = parse_causal_intent("下一步怎么做?")
    assert ci.has_intervention is False
    assert ci.has_randomization_signal is False
    assert ci.wants_lift_or_effect is False
    assert ci.detected_outcome_terms == ()


def test_why_drop_is_descriptive_no_intervention():
    # "为什么下降" 只含 lift 词(下降),不含干预/随机化 → 不构成因果主张
    ci = parse_causal_intent("收入为什么下降?")
    assert ci.wants_lift_or_effect is True
    assert ci.has_intervention is False
    assert ci.has_randomization_signal is False


def test_non_string_returns_empty_intent():
    ci = parse_causal_intent(123)  # type: ignore[arg-type]
    assert ci.has_intervention is False
    assert ci.detected_outcome_terms == ()


# ----------------------------- claim_level 推断 -----------------------------


def test_claim_level_experiment_when_randomized():
    ci = parse_causal_intent("A/B 实验,实验组是否提升留存")
    assert infer_claim_level(ci, has_explicit_assumptions=False) is ClaimLevel.EXPERIMENTAL


def test_claim_level_causal_assumption_when_intervention_plus_assumptions():
    ci = parse_causal_intent("X 是否导致 Y")
    assert ci.has_intervention is True
    assert infer_claim_level(ci, has_explicit_assumptions=True) is ClaimLevel.CAUSAL_ASSUMPTION


def test_claim_level_associational_when_intervention_no_assumptions():
    ci = parse_causal_intent("X 是否导致 Y")
    assert infer_claim_level(ci, has_explicit_assumptions=False) is ClaimLevel.ASSOCIATIONAL


def test_correlation_only_is_associational_not_causal():
    ci = parse_causal_intent("收入与广告支出相关")
    assert ci.has_observation_marker is True
    # 不得升级为 causal:无随机化、无显式假设 → ASSOCIATIONAL
    assert infer_claim_level(ci, has_explicit_assumptions=True) is ClaimLevel.ASSOCIATIONAL


def test_claim_level_descriptive_when_no_causal_signal():
    ci = parse_causal_intent("给我一份上周的销售日报")
    assert infer_claim_level(ci, has_explicit_assumptions=False) is ClaimLevel.DESCRIPTIVE


def test_experiment_request_does_not_mark_assumptions_explicit():
    # 关键不变量:实验请求被识别,但"假设"是否 explicit 取决于调用方传入的 has_explicit_assumptions,
    # 解析器绝不把推断当显式。这里仅断言 claim_level 不因检测到实验信号就变 CAUSAL_ASSUMPTION。
    ci = parse_causal_intent("实验组是否提高了留存?")
    assert ci.has_randomization_signal is True
    assert infer_claim_level(ci, has_explicit_assumptions=False) is ClaimLevel.EXPERIMENTAL
