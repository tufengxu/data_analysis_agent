"""因果决策领域层:A/B 实验统计与决策(纯 stdlib ``math``)。

实现 Stage 1 的效应估计、样本比例失衡(SRM)检验、护栏判定与有界决策分类。全部确定性、
可复现:正态近似 z 检验(95% CI)、卡方 SRM(硬编码 α=0.05 临界表)。**退化数据**
(SE=0、空组、pooled∈{0,1})不算 z/p,直接标 ``degenerate`` → 决策走 INCONCLUSIVE,
绝不产出伪造 p 值。

数学承载者:本模块的边界用例由 ``tests/test_experiment_readout.py`` 作为契约锁定。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from data_analysis_agent.causal.model import (
    ActionPlan,
    ActionRecommendation,
    CausalContract,
    ContrastResult,
    DecisionLevel,
    EffectEstimate,
    ExperimentReadout,
    GuardrailResult,
    OutcomeKind,
    SegmentBreakdown,
    SRMResult,
)

__all__ = [
    "compute_effect",
    "compute_srm",
    "compute_guardrail",
    "classify_contrast",
    "aggregate",
    "compute_readout",
    "build_action_plan",
]

# 95% 双侧正态分位数;卡方 α=0.05 临界表(df→临界值,df=k-1)。
_Z_975 = 1.959963984540054
_SQRT2 = math.sqrt(2.0)
_CHI2_CRIT_05: dict[int, float] = {
    1: 3.841,
    2: 5.991,
    3: 7.815,
    4: 9.488,
    5: 11.070,
    6: 12.592,
    7: 14.067,
    8: 15.507,
    9: 16.919,
    10: 18.307,
}


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v)


def _is_binary(vals: Sequence[float]) -> bool:
    return len(vals) > 0 and all(v in (0, 0.0, 1, 1.0) for v in vals)


def _numeric(vals: Sequence[object]) -> list[float]:
    return [v for v in vals if _is_number(v)]  # type: ignore[misc]


def compute_effect(
    col: str,
    control_vals: Sequence[object],
    treatment_vals: Sequence[object],
    kind: OutcomeKind,
) -> EffectEstimate:
    """单对比、单结果变量的效应估计。

    AUTO:值⊆{0,1}→PROPORTION,否则 MEAN。强制 PROPORTION 但非二元 → 抛 ValueError。
    """
    c = _numeric(control_vals)
    t = _numeric(treatment_vals)
    n_c, n_t = len(c), len(t)
    if n_c == 0 or n_t == 0:
        return EffectEstimate(
            outcome_column=col,
            outcome_kind=kind,
            control_n=n_c,
            treatment_n=n_t,
            degenerate=True,
            notes=("empty_group",),
        )

    # 解析口径(AUTO → 据数据判定)
    if kind is OutcomeKind.AUTO:
        resolved = OutcomeKind.PROPORTION if _is_binary(c + t) else OutcomeKind.MEAN
    else:
        resolved = kind
    if resolved is OutcomeKind.PROPORTION and not _is_binary(c + t):
        raise ValueError(f"outcome_kind=proportion 要求结果列 {col} 取值 ⊆ {{0,1}},实际含非二元值")

    notes: list[str] = ["welch_z_approx"]

    if resolved is OutcomeKind.PROPORTION:
        x_c, x_t = math.fsum(c), math.fsum(t)
        p_c, p_t = x_c / n_c, x_t / n_t
        pooled = (x_c + x_t) / (n_c + n_t)
        effect = p_t - p_c
        relative = effect / p_c if p_c != 0 else None
        control_mean, treatment_mean = p_c, p_t
        if pooled == 0 or pooled == 1:
            se, z, p_value = 0.0, None, None
            ci_lower = ci_upper = effect
            degenerate = True
        else:
            se = math.sqrt(pooled * (1 - pooled) * (1 / n_c + 1 / n_t))
            if se == 0:
                z, p_value = None, None
                ci_lower = ci_upper = effect
                degenerate = True
            else:
                z = effect / se
                p_value = max(0.0, min(1.0, math.erfc(abs(z) / _SQRT2)))
                ci_lower = effect - _Z_975 * se
                ci_upper = effect + _Z_975 * se
                degenerate = False
            if pooled * (n_c + n_t) < 5:
                notes.append("low_cell_count")
    else:  # MEAN
        mean_c = math.fsum(c) / n_c
        mean_t = math.fsum(t) / n_t
        var_c = math.fsum((x - mean_c) ** 2 for x in c) / (n_c - 1) if n_c > 1 else 0.0
        var_t = math.fsum((x - mean_t) ** 2 for x in t) / (n_t - 1) if n_t > 1 else 0.0
        effect = mean_t - mean_c
        relative = effect / mean_c if mean_c != 0 else None
        control_mean, treatment_mean = mean_c, mean_t
        se = math.sqrt(var_t / n_t + var_c / n_c)
        if se == 0:
            z, p_value = None, None
            ci_lower = ci_upper = effect
            degenerate = True
        else:
            z = effect / se
            p_value = max(0.0, min(1.0, math.erfc(abs(z) / _SQRT2)))
            ci_lower = effect - _Z_975 * se
            ci_upper = effect + _Z_975 * se
            degenerate = False

    significant: bool | None
    if degenerate:
        significant = None
    else:
        significant = (ci_lower is not None and ci_lower > 0) or (
            ci_upper is not None and ci_upper < 0
        )

    return EffectEstimate(
        outcome_column=col,
        outcome_kind=resolved,
        control_n=n_c,
        treatment_n=n_t,
        control_mean=control_mean,
        treatment_mean=treatment_mean,
        effect=effect,
        relative_effect=relative,
        se=se,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        z=z,
        p_value=p_value,
        significant=significant,
        degenerate=degenerate,
        notes=tuple(notes),
    )


def compute_srm(
    arms: Sequence[str],
    observed: Sequence[int],
    expected_ratio: Sequence[float] | None = None,
    alpha: float = 0.05,
) -> SRMResult:
    """样本比例失衡卡方检验(等比分配为默认期望)。"""
    k = len(arms)
    arms_t = tuple(arms)
    obs_t = tuple(observed)
    total = sum(obs_t)
    if k < 2:
        return SRMResult(arms_t, obs_t, (), None, None, None, False, alpha, ("single_arm",))
    if total == 0:
        return SRMResult(arms_t, obs_t, (), None, None, None, False, alpha, ("empty",))
    if expected_ratio and len(expected_ratio) == k:
        s = math.fsum(expected_ratio)
        ratios = tuple(r / s for r in expected_ratio) if s > 0 else tuple(1.0 / k for _ in range(k))
    else:
        ratios = tuple(1.0 / k for _ in range(k))
    exp = tuple(r * total for r in ratios)
    if any(e <= 0 for e in exp):
        return SRMResult(
            arms_t, obs_t, exp, None, None, None, False, alpha, ("zero_expected_cell",)
        )
    chi2 = math.fsum((o - e) ** 2 / e for o, e in zip(obs_t, exp, strict=True))
    df = k - 1
    crit = _CHI2_CRIT_05.get(df)
    if crit is None:
        return SRMResult(arms_t, obs_t, exp, chi2, df, None, False, alpha, ("df_exceeds_table",))
    return SRMResult(arms_t, obs_t, exp, chi2, df, crit, chi2 > crit, alpha, ())


def compute_guardrail(
    col: str,
    control_vals: Sequence[object],
    treatment_vals: Sequence[object],
    kind: OutcomeKind,
    direction: str = "higher_is_worse",
    tolerance: float = 0.0,
) -> GuardrailResult:
    """护栏效应 + 破阈判定(复用 compute_effect)。"""
    est = compute_effect(col, control_vals, treatment_vals, kind)
    if est.degenerate:
        breached = False
    elif direction == "lower_is_worse":
        breached = est.ci_upper is not None and est.ci_upper < -tolerance
    else:  # higher_is_worse(默认)
        breached = est.ci_lower is not None and est.ci_lower > tolerance
    return GuardrailResult(col, est, direction, tolerance, breached)


def classify_contrast(
    estimate: EffectEstimate,
    guardrails: Sequence[GuardrailResult],
    srm: SRMResult | None,
    min_n: int,
    threshold: float,
) -> tuple[DecisionLevel, list[str]]:
    """单对比决策(优先级从上到下)。"""
    if estimate.control_n < min_n or estimate.treatment_n < min_n:
        return DecisionLevel.NEEDS_MORE_DATA, ["below_min_sample_size"]
    if estimate.degenerate:
        return DecisionLevel.INCONCLUSIVE, ["degenerate_zero_se"]
    if srm is not None and srm.srm_detected:
        return DecisionLevel.INCONCLUSIVE, ["srm_contamination"]
    breaches = [g.column for g in guardrails if g.breached]
    if breaches:
        return DecisionLevel.DO_NOT_SHIP, [f"guardrail_breach:{c}" for c in breaches]
    if estimate.ci_upper is not None and estimate.ci_upper < 0:
        return DecisionLevel.DO_NOT_SHIP, ["significant_negative_effect"]
    ci_pos = estimate.ci_lower is not None and estimate.ci_lower > 0
    thr_met = threshold <= 0 or (
        estimate.relative_effect is not None and estimate.relative_effect >= threshold
    )
    if ci_pos and thr_met:
        return DecisionLevel.SHIP, ["significant_positive_and_threshold_met"]
    reason = "threshold_not_met" if (ci_pos and not thr_met) else "ci_crosses_zero"
    return DecisionLevel.INCONCLUSIVE, [reason]


def aggregate(
    contrasts: Sequence[ContrastResult],
    srm: SRMResult | None,
) -> tuple[DecisionLevel, list[str]]:
    """聚合多对比决策(格序:do_not_ship > needs_more_data > inconclusive > ship)。"""
    if srm is not None and srm.srm_detected:
        return DecisionLevel.INCONCLUSIVE, ["srm_contamination"]
    decisions = [c.decision for c in contrasts]
    if not decisions:
        return DecisionLevel.INCONCLUSIVE, ["no_contrasts"]
    if DecisionLevel.DO_NOT_SHIP in decisions:
        return DecisionLevel.DO_NOT_SHIP, ["at_least_one_do_not_ship"]
    if DecisionLevel.NEEDS_MORE_DATA in decisions:
        return DecisionLevel.NEEDS_MORE_DATA, ["at_least_one_needs_more_data"]
    if DecisionLevel.INCONCLUSIVE in decisions:
        return DecisionLevel.INCONCLUSIVE, ["at_least_one_inconclusive"]
    return DecisionLevel.SHIP, ["all_contrasts_ship"]


# ----------------------------- 读出编排 -----------------------------


def _values_by_arm(
    columns: Mapping[str, Sequence[object]],
    group_column: str,
    target_column: str,
    arm: str,
) -> list[object]:
    group_col = columns.get(group_column, ())
    target_col = columns.get(target_column, ())
    return [
        target_col[i] for i in range(min(len(group_col), len(target_col))) if group_col[i] == arm
    ]


def _count_arm(group_col: Sequence[object], arm: str) -> int:
    return sum(1 for v in group_col if v == arm)


def compute_readout(
    *,
    contract_question: str,
    control_arm: str,
    treatment_arms: Sequence[str],
    group_column: str,
    outcome_column: str,
    columns: Mapping[str, Sequence[object]],
    outcome_kind: OutcomeKind = OutcomeKind.AUTO,
    guardrail_columns: Sequence[str] = (),
    guardrail_directions: Mapping[str, str] | None = None,
    segment_columns: Sequence[str] = (),
    expected_ratio: Sequence[float] | None = None,
    decision_threshold: float = 0.0,
    min_sample_size: int = 30,
) -> ExperimentReadout:
    """编排一次实验读出:前置校验 → SRM → 每对比效应/护栏/决策 → 聚合。"""
    group_seq = columns.get(group_column, ())
    arms_present = set(group_seq)
    total_n = len(group_seq)

    # 前置:必需列/臂缺失 → NEEDS_MORE_DATA,不计算对比
    missing: list[str] = []
    if outcome_column not in columns:
        missing.append("outcome_column")
    if control_arm not in arms_present:
        missing.append("control_arm")
    for ta in treatment_arms:
        if ta not in arms_present:
            missing.append(f"treatment_arm:{ta}")
    if missing:
        return ExperimentReadout(
            contract_question=contract_question,
            control_arm=control_arm,
            outcome_column=outcome_column,
            outcome_kind=outcome_kind,
            aggregate_decision=DecisionLevel.NEEDS_MORE_DATA,
            aggregate_reasons=tuple(f"missing_{m}" for m in missing),
            min_sample_size=min_sample_size,
            decision_threshold=decision_threshold,
            total_n=total_n,
        )

    # 解析结果口径(AUTO 据全样本判定);强制 PROPORTION 但非二元 → ValueError
    resolved_kind = outcome_kind
    if outcome_column in columns:
        numeric_outcome = _numeric(columns.get(outcome_column, ()))
        if outcome_kind is OutcomeKind.AUTO:
            resolved_kind = (
                OutcomeKind.PROPORTION if _is_binary(numeric_outcome) else OutcomeKind.MEAN
            )
        elif outcome_kind is OutcomeKind.PROPORTION and not _is_binary(numeric_outcome):
            raise ValueError(
                f"outcome_kind=proportion 要求结果列 {outcome_column} 取值 ⊆ {{0,1}},实际含非二元值"
            )

    # SRM(对照 + 所有处理臂)
    all_arms: tuple[str, ...] = (control_arm, *treatment_arms)
    observed = tuple(_count_arm(group_seq, arm) for arm in all_arms)
    srm = compute_srm(all_arms, observed, expected_ratio)

    contrasts: list[ContrastResult] = []
    for ta in treatment_arms:
        c_vals = _values_by_arm(columns, group_column, outcome_column, control_arm)
        t_vals = _values_by_arm(columns, group_column, outcome_column, ta)
        est = compute_effect(outcome_column, c_vals, t_vals, resolved_kind)

        guardrails: list[GuardrailResult] = []
        for gc in guardrail_columns:
            if gc not in columns:
                continue
            direction = (guardrail_directions or {}).get(gc, "higher_is_worse")
            guardrails.append(
                compute_guardrail(
                    gc,
                    _values_by_arm(columns, group_column, gc, control_arm),
                    _values_by_arm(columns, group_column, gc, ta),
                    OutcomeKind.AUTO,
                    direction,
                )
            )

        segments: list[SegmentBreakdown] = [
            SegmentBreakdown(column=sc, note="descriptive only; Stage1 不做分群检验")
            for sc in segment_columns
            if sc in columns
        ]

        decision, reasons = classify_contrast(
            est, guardrails, srm, min_sample_size, decision_threshold
        )
        contrasts.append(
            ContrastResult(
                treatment_arm=ta,
                outcome_estimate=est,
                guardrails=tuple(guardrails),
                segments=tuple(segments),
                decision=decision,
                decision_reasons=tuple(reasons),
            )
        )

    agg_decision, agg_reasons = aggregate(contrasts, srm)
    return ExperimentReadout(
        contract_question=contract_question,
        control_arm=control_arm,
        outcome_column=outcome_column,
        outcome_kind=resolved_kind,
        contrasts=tuple(contrasts),
        srm=srm,
        aggregate_decision=agg_decision,
        aggregate_reasons=tuple(agg_reasons),
        min_sample_size=min_sample_size,
        decision_threshold=decision_threshold,
        total_n=total_n,
    )


# ----------------------------- 行动计划 -----------------------------


def build_action_plan(
    readout: ExperimentReadout,
    contract: CausalContract | None = None,
) -> ActionPlan:
    """把读出的有界决策转成带机制/假设/监控/回滚/反驳的行动计划(确定性)。

    每条建议都挂在证据(reasons)/假设/监控上;NEVER 无依据推荐行动。SRM/护栏破阈
    即使在 inconclusive 下也作为可见风险列出,但不升级为 ship。
    """
    reasons: set[str] = set(readout.aggregate_reasons)
    for c in readout.contrasts:
        reasons.update(c.decision_reasons)

    recs: list[ActionRecommendation] = []
    risks: list[str] = []

    if "srm_contamination" in reasons:
        recs.append(
            ActionRecommendation(
                code="fix_srm",
                rationale="分流完整性存疑:排查分流日志与随机化实现,修正后再决策",
                precondition="SRM 解决前不据 lift 行动",
            )
        )
        risks.append("SRM:当前 lift 不可信")

    for c in readout.contrasts:
        for g in c.guardrails:
            if g.breached:
                recs.append(
                    ActionRecommendation(
                        code="investigate_guardrail",
                        target_arm=c.treatment_arm,
                        rationale=f"护栏 {g.column} 破阈({g.unfavorable_direction})",
                    )
                )
                risks.append(f"护栏 {g.column} 破阈")

    decision = readout.aggregate_decision
    if decision is DecisionLevel.SHIP:
        recs.append(
            ActionRecommendation(
                code="ship",
                rationale="效应显著正向且过阈值,护栏未破阈",
                precondition="上线后持续监控护栏与 SRM;触发回滚阈值即回退",
            )
        )
    elif decision is DecisionLevel.DO_NOT_SHIP:
        recs.append(
            ActionRecommendation(code="hold", rationale="效应显著负向或护栏破阈,不建议上线")
        )
    elif decision is DecisionLevel.NEEDS_MORE_DATA:
        if any("missing_" in r for r in reasons):
            recs.append(
                ActionRecommendation(
                    code="drop_arm", rationale="存在缺失的处理臂/结果列,核实数据管道后再分析"
                )
            )
        else:
            recs.append(
                ActionRecommendation(
                    code="add_power", rationale="样本不足,延长实验或扩大分流以提升功效"
                )
            )
    else:  # INCONCLUSIVE
        recs.append(
            ActionRecommendation(
                code="hold", rationale="效应不显著或受 SRM/退化影响,暂缓决策;必要时补样本"
            )
        )

    assumptions = contract.business_assumptions if contract is not None else ()
    refutations = ("安慰剂/零处理对照", "不同时间窗口的稳定性", "负控(反转处理)")

    # 去重建议(按 code+target_arm+rationale),保序;这样同一臂上多个护栏破阈
    # (investigate_guardrail 但 rationale 不同)各自保留,仅合并真正重复。风险去重保序。
    seen: set[tuple[str, str | None, str]] = set()
    deduped: list[ActionRecommendation] = []
    for r in recs:
        key = (r.code, r.target_arm, r.rationale)
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return ActionPlan(
        decision=decision,
        recommendations=tuple(deduped),
        assumptions=assumptions,
        refutations=refutations,
        open_risks=tuple(dict.fromkeys(risks)),
    )
