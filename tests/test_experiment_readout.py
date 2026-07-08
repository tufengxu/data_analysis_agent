"""causal.experiment:效应估计/SRM/护栏/决策的数学正确性(Stage1 契约)。

断言方法(ADR 0005):不断"业务数值锚点",而是断结构 + 与手算小样本一致的统计量。
所有边界用例(§7.8)在此锁定。
"""

from __future__ import annotations

import math

import pytest

from data_analysis_agent.causal.experiment import (
    aggregate,
    classify_contrast,
    compute_effect,
    compute_guardrail,
    compute_readout,
    compute_srm,
)
from data_analysis_agent.causal.model import DecisionLevel, OutcomeKind

_Z = 1.959963984540054


# ----------------------------- compute_effect: 比例 -----------------------------


def test_proportion_effect_matches_hand_computation():
    # control: 2/10=0.2; treatment: 7/10=0.7; pooled=9/20=0.45
    c = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1]
    t = [1, 1, 1, 1, 1, 1, 1, 0, 0, 0]
    est = compute_effect("conv", c, t, OutcomeKind.AUTO)
    assert est.outcome_kind is OutcomeKind.PROPORTION
    assert est.control_n == 10 and est.treatment_n == 10
    assert est.control_mean == pytest.approx(0.2)
    assert est.treatment_mean == pytest.approx(0.7)
    assert est.effect == pytest.approx(0.5)
    pooled = 0.45
    se = math.sqrt(pooled * (1 - pooled) * (1 / 10 + 1 / 10))
    assert est.se == pytest.approx(se)
    assert est.ci_lower == pytest.approx(0.5 - _Z * se)
    assert est.ci_upper == pytest.approx(0.5 + _Z * se)
    assert est.z == pytest.approx(0.5 / se)
    assert est.p_value == pytest.approx(math.erfc(abs(0.5 / se) / math.sqrt(2)))
    assert est.significant is True  # CI 下界 > 0
    assert "welch_z_approx" in est.notes


def test_auto_detects_mean_for_non_binary():
    est = compute_effect("rev", [1.0, 2.0, 3.0], [4.0, 5.0, 6.0], OutcomeKind.AUTO)
    assert est.outcome_kind is OutcomeKind.MEAN


def test_forced_proportion_on_non_binary_raises():
    with pytest.raises(ValueError):
        compute_effect("rev", [1.0, 2.0, 3.0], [4.0, 5.0, 6.0], OutcomeKind.PROPORTION)


# ----------------------------- compute_effect: 均值 -----------------------------


def test_mean_effect_matches_hand_computation():
    c = [1.0, 2.0, 3.0, 4.0, 5.0]  # mean 3, var 2.5
    t = [2.0, 4.0, 6.0, 8.0, 10.0]  # mean 6, var 10
    est = compute_effect("rev", c, t, OutcomeKind.MEAN)
    assert est.effect == pytest.approx(3.0)
    var_c = 2.5
    var_t = 10.0
    se = math.sqrt(var_t / 5 + var_c / 5)
    assert est.se == pytest.approx(se)
    assert est.relative_effect == pytest.approx(1.0)  # 3/3


# ----------------------------- compute_effect: 边界/退化 -----------------------------


def test_empty_group_is_degenerate():
    est = compute_effect("x", [], [1, 0, 1], OutcomeKind.AUTO)
    assert est.degenerate is True
    assert est.control_n == 0
    assert "empty_group" in est.notes
    assert est.z is None and est.p_value is None


def test_proportion_pooled_zero_degenerate():
    # 双方全 0 事件 → pooled=0 → SE=0 → 退化,不算 z/p
    est = compute_effect("conv", [0, 0, 0], [0, 0, 0], OutcomeKind.PROPORTION)
    assert est.degenerate is True
    assert est.z is None and est.p_value is None
    assert est.significant is None
    assert est.effect == pytest.approx(0.0)


def test_proportion_pooled_one_degenerate():
    est = compute_effect("conv", [1, 1, 1], [1, 1, 1], OutcomeKind.PROPORTION)
    assert est.degenerate is True
    assert est.z is None


def test_mean_zero_variance_equal_means_degenerate():
    est = compute_effect("x", [5.0, 5.0, 5.0], [5.0, 5.0, 5.0], OutcomeKind.MEAN)
    assert est.degenerate is True
    assert est.effect == pytest.approx(0.0)
    assert est.z is None  # 不产伪造 p


def test_mean_zero_variance_differing_means_degenerate_no_spurious_p():
    # 双方差 0 但均值异:绝不能算出 z=±inf / p≈0
    est = compute_effect("x", [5.0, 5.0, 5.0], [9.0, 9.0, 9.0], OutcomeKind.MEAN)
    assert est.degenerate is True
    assert est.z is None
    assert est.p_value is None
    assert est.effect == pytest.approx(4.0)


def test_relative_none_when_control_zero_proportion():
    est = compute_effect("conv", [0, 0, 0], [1, 1, 1], OutcomeKind.PROPORTION)
    assert est.relative_effect is None
    assert est.effect == pytest.approx(1.0)


def test_relative_none_when_control_zero_mean():
    est = compute_effect("x", [0.0, 0.0], [2.0, 4.0], OutcomeKind.MEAN)
    assert est.relative_effect is None


def test_low_cell_count_note():
    est = compute_effect("conv", [0, 0], [1, 1], OutcomeKind.PROPORTION)
    assert "low_cell_count" in est.notes  # pooled*4 = 2 < 5


def test_non_numeric_values_filtered():
    est = compute_effect("x", [1, "na", 3, None], [2, 4, "x", 6], OutcomeKind.MEAN)
    assert est.control_n == 2 and est.treatment_n == 3


# ----------------------------- compute_srm -----------------------------


def test_srm_balanced_not_detected():
    srm = compute_srm(("a", "b"), (100, 100))
    assert srm.srm_detected is False
    assert srm.chi_square == pytest.approx(0.0)


def test_srm_severe_imbalance_detected():
    # 300 vs 100:N=400,期望各 200 → chi2=100 >> 3.841(df=1)
    srm = compute_srm(("a", "b"), (300, 100))
    assert srm.srm_detected is True
    assert srm.df == 1
    assert srm.critical_value == pytest.approx(3.841)


def test_srm_real_seed_not_significant():
    # 真实种子 255/218/227:chi2≈3.19 < 5.991(df=2)→ Stage1 在 α=0.05 下不判 SRM
    srm = compute_srm(("control", "variant_a", "variant_b"), (255, 218, 227))
    assert srm.srm_detected is False
    assert srm.chi_square == pytest.approx(3.19, abs=0.05)


def test_srm_single_arm_note():
    srm = compute_srm(("a",), (100,))
    assert srm.srm_detected is False
    assert "single_arm" in srm.notes


def test_srm_empty_note():
    srm = compute_srm(("a", "b"), (0, 0))
    assert "empty" in srm.notes


def test_srm_zero_expected_cell_note():
    srm = compute_srm(("a", "b"), (100, 100), expected_ratio=(1.0, 0.0))
    assert "zero_expected_cell" in srm.notes
    assert srm.srm_detected is False


# ----------------------------- compute_guardrail -----------------------------


def test_guardrail_higher_is_worse_breach():
    # treatment 崩溃率显著升高 → ci_lower>0 → breached
    g = compute_guardrail("crash", [0] * 50, [1] * 50, OutcomeKind.AUTO, "higher_is_worse")
    assert g.breached is True


def test_guardrail_no_breach_when_degenerate():
    g = compute_guardrail("crash", [], [1, 1], OutcomeKind.AUTO, "higher_is_worse")
    assert g.breached is False


# ----------------------------- classify_contrast -----------------------------


def _est(
    effect: float, ci_lower: float, ci_upper: float, *, degenerate=False, relative=None, n=100
):
    from data_analysis_agent.causal.model import EffectEstimate

    return EffectEstimate(
        outcome_column="x",
        outcome_kind=OutcomeKind.MEAN,
        control_n=n,
        treatment_n=n,
        effect=effect,
        relative_effect=relative,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        degenerate=degenerate,
        notes=("welch_z_approx",),
    )


def test_classify_below_min_sample():
    est = _est(1.0, 0.5, 1.5, n=5)
    d, r = classify_contrast(est, [], None, min_n=30, threshold=0.0)
    assert d is DecisionLevel.NEEDS_MORE_DATA
    assert "below_min_sample_size" in r


def test_classify_degenerate():
    est = _est(0.0, 0.0, 0.0, degenerate=True)
    d, _ = classify_contrast(est, [], None, min_n=30, threshold=0.0)
    assert d is DecisionLevel.INCONCLUSIVE


def test_classify_srm_contamination():
    srm = compute_srm(("c", "t"), (300, 100))
    est = _est(1.0, 0.5, 1.5)
    d, r = classify_contrast(est, [], srm, min_n=30, threshold=0.0)
    assert d is DecisionLevel.INCONCLUSIVE
    assert "srm_contamination" in r


def test_classify_significant_negative():
    est = _est(-1.0, -1.5, -0.5)
    d, r = classify_contrast(est, [], None, min_n=30, threshold=0.0)
    assert d is DecisionLevel.DO_NOT_SHIP
    assert "significant_negative_effect" in r


def test_classify_ship():
    est = _est(0.5, 0.2, 0.8, relative=0.5)
    d, r = classify_contrast(est, [], None, min_n=30, threshold=0.0)
    assert d is DecisionLevel.SHIP


def test_classify_threshold_not_met():
    est = _est(0.05, 0.02, 0.08, relative=0.05)  # 显著正但 relative < 阈值 0.1
    d, r = classify_contrast(est, [], None, min_n=30, threshold=0.1)
    assert d is DecisionLevel.INCONCLUSIVE
    assert "threshold_not_met" in r


def test_classify_ci_crosses_zero():
    est = _est(0.05, -0.1, 0.2)
    d, r = classify_contrast(est, [], None, min_n=30, threshold=0.0)
    assert d is DecisionLevel.INCONCLUSIVE
    assert "ci_crosses_zero" in r


# ----------------------------- aggregate -----------------------------


def _contrast(decision: DecisionLevel):
    from data_analysis_agent.causal.model import ContrastResult

    return ContrastResult(
        treatment_arm="t",
        outcome_estimate=_est(0.0, 0.0, 0.0),
        decision=decision,
    )


def test_aggregate_srm_overrides():
    srm = compute_srm(("c", "t"), (300, 100))
    d, r = aggregate([_contrast(DecisionLevel.SHIP)], srm)
    assert d is DecisionLevel.INCONCLUSIVE
    assert "srm_contamination" in r


def test_aggregate_do_not_ship_beats_needs_more_data():
    # 格序:do_not_ship 高于 needs_more_data
    d, _ = aggregate(
        [_contrast(DecisionLevel.DO_NOT_SHIP), _contrast(DecisionLevel.NEEDS_MORE_DATA)], None
    )
    assert d is DecisionLevel.DO_NOT_SHIP


def test_aggregate_all_ship():
    d, _ = aggregate([_contrast(DecisionLevel.SHIP), _contrast(DecisionLevel.SHIP)], None)
    assert d is DecisionLevel.SHIP


def test_aggregate_inconclusive_beats_ship():
    d, _ = aggregate([_contrast(DecisionLevel.INCONCLUSIVE), _contrast(DecisionLevel.SHIP)], None)
    assert d is DecisionLevel.INCONCLUSIVE


def test_aggregate_do_not_ship_beats_ship():
    d, _ = aggregate([_contrast(DecisionLevel.DO_NOT_SHIP), _contrast(DecisionLevel.SHIP)], None)
    assert d is DecisionLevel.DO_NOT_SHIP


def test_aggregate_no_contrasts():
    d, _ = aggregate([], None)
    assert d is DecisionLevel.INCONCLUSIVE


# ----------------------------- compute_readout: 编排 -----------------------------


def _cols(group: list[str], outcome: list[float], **extra: list) -> dict:
    cols: dict = {"variant": group, "y": outcome}
    cols.update(extra)
    return cols


def test_readout_ship_path_two_arm():
    # control p≈0.5,treatment p≈0.9 → 显著正 → SHIP
    group = ["control"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 1, 0] * 10  # control 25/50, t 45/50
    ro = compute_readout(
        contract_question="q",
        control_arm="control",
        treatment_arms=("t",),
        group_column="variant",
        outcome_column="y",
        columns=_cols(group, outcome),
        decision_threshold=0.0,
        min_sample_size=30,
    )
    assert len(ro.contrasts) == 1
    assert ro.contrasts[0].decision is DecisionLevel.SHIP
    assert ro.aggregate_decision is DecisionLevel.SHIP


def test_readout_missing_outcome_column():
    ro = compute_readout(
        contract_question="q",
        control_arm="control",
        treatment_arms=("t",),
        group_column="variant",
        outcome_column="absent",
        columns={"variant": ["control", "t"]},
    )
    assert ro.aggregate_decision is DecisionLevel.NEEDS_MORE_DATA
    assert any("missing_outcome_column" in r for r in ro.aggregate_reasons)


def test_readout_missing_arm():
    ro = compute_readout(
        contract_question="q",
        control_arm="control",
        treatment_arms=("ghost",),
        group_column="variant",
        outcome_column="y",
        columns={"variant": ["control", "control"], "y": [0.0, 1.0]},
    )
    assert ro.aggregate_decision is DecisionLevel.NEEDS_MORE_DATA
    assert any("treatment_arm:ghost" in r for r in ro.aggregate_reasons)


def test_readout_three_arm():
    group = ["c"] * 40 + ["a"] * 40 + ["b"] * 40
    outcome = [0, 1] * 20 + [1, 1, 0, 1] * 10 + [1, 1, 1, 0] * 10
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("a", "b"),
        group_column="variant",
        outcome_column="y",
        columns=_cols(group, outcome),
        min_sample_size=30,
    )
    assert len(ro.contrasts) == 2
    assert {c.treatment_arm for c in ro.contrasts} == {"a", "b"}
    assert ro.srm is not None and ro.srm.df == 2


def test_readout_mean_outcome_kind_resolved():
    group = ["c"] * 40 + ["t"] * 40
    outcome = [1.0, 2.0, 3.0, 4.0] * 10 + [5.0, 6.0, 7.0, 8.0] * 10
    ro = compute_readout(
        contract_question="q",
        control_arm="c",
        treatment_arms=("t",),
        group_column="variant",
        outcome_column="y",
        columns=_cols(group, outcome),
        min_sample_size=30,
    )
    assert ro.outcome_kind is OutcomeKind.MEAN
    assert ro.contrasts[0].outcome_estimate.outcome_kind is OutcomeKind.MEAN


def test_readout_deterministic():
    group = ["c"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 0, 1] * 10
    kwargs = {
        "contract_question": "q",
        "control_arm": "c",
        "treatment_arms": ("t",),
        "group_column": "variant",
        "outcome_column": "y",
        "columns": _cols(group, outcome),
    }
    assert compute_readout(**kwargs) == compute_readout(**kwargs)
