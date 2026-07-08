"""因果决策领域层:causal 结果 → reporting.ReportDocument 适配。

本模块是 causal 包中**唯一**导入 ``reporting.contract`` 的模块(见 ADR 0010 / drift 规则)。
把 ``ExperimentReadout`` + ``CausalContract`` + ``CausalQAReport`` (+ 可选 ``ActionPlan``)
转换成 ``ReportDocument``,其块序列满足 reporting QA 的 ``_check_causal`` 规则:每个 FINDING
块紧跟一个 CAVEAT 块,且 FINDING 正文用中性措辞(避免强因果动词),因果语留给 CAVEAT。

``to_reporting_readiness`` 把 6 态 ``CausalReadiness`` 映射到 reporting 三态 ``Readiness``,
供调用方按 reporting 词汇判定就绪。
"""

from __future__ import annotations

from data_analysis_agent.causal.model import (
    ActionPlan,
    CausalContract,
    CausalQAReport,
    CausalReadiness,
    ContrastResult,
    ExperimentReadout,
)
from data_analysis_agent.reporting.contract import (
    BlockRole,
    ReportBlock,
    ReportContract,
    ReportDocument,
    ReportType,
)
from data_analysis_agent.reporting.qa import Readiness

__all__ = ["to_reporting_readiness", "to_report_document"]

_READINESS_MAP: dict[CausalReadiness, Readiness] = {
    CausalReadiness.NOT_CAUSAL: Readiness.DRAFT,
    CausalReadiness.BLOCKED: Readiness.DRAFT,
    CausalReadiness.NEEDS_ASSUMPTIONS: Readiness.NEEDS_REVIEW,
    CausalReadiness.NEEDS_DATA: Readiness.NEEDS_REVIEW,
    CausalReadiness.ASSUMPTION_READY: Readiness.READY,
    CausalReadiness.EXPERIMENT_READY: Readiness.READY,
}


def to_reporting_readiness(causal_readiness: CausalReadiness) -> Readiness:
    """6 态 CausalReadiness → reporting 三态 Readiness。"""
    return _READINESS_MAP[causal_readiness]


def to_report_document(
    *,
    readout: ExperimentReadout,
    contract: CausalContract,
    qa_report: CausalQAReport,
    action_plan: ActionPlan | None = None,
    generated_at: str | None = None,
) -> ReportDocument:
    """把 causal 读出 + 契约 + QA 转成 ReportDocument(FINDING 紧跟 CAVEAT)。"""
    blocks: list[ReportBlock] = []
    blocks.append(
        ReportBlock(block_id="causal_header", role=BlockRole.HEADER, heading=_title(contract))
    )
    blocks.append(_exec_summary(contract, qa_report, readout))
    blocks.append(_data_context(readout))
    if readout.contrasts:
        blocks.append(_kpi_strip(readout))
    for idx, contrast in enumerate(readout.contrasts):
        blocks.append(_finding(contrast, idx, readout))
        blocks.append(_contrast_caveat(contrast, idx, readout))  # 紧跟 CAVEAT
    if action_plan is not None:
        blocks.append(_recommendation(action_plan))
    blocks.append(_assumption_caveat(contract, qa_report))
    blocks.append(_source_metadata(readout))

    return ReportDocument(
        title=_title(contract),
        blocks=tuple(blocks),
        contract=_to_reporting_contract(contract),
        generated_at=generated_at,
        data_scope=_data_scope(readout),
    )


def _to_reporting_contract(contract: CausalContract) -> ReportContract:
    """从 CausalContract 派生最小 ReportContract(承载 question/field_sources/missing_context)。

    Stage1 适配器是结构中间产物:它保证 FINDING→CAVEAT 邻接与就绪映射(核心不变量),
    但不单独满足 reporting QA 的全部 traceability/evidence 规则——完整 traceability 由
    agent 把本读出织入整份报告时补齐。这里只把因果契约已有的溯源字段透传过去。
    """
    return ReportContract(
        question=contract.question,
        report_type=ReportType.AD_HOC,
        field_sources=contract.field_sources,
        missing_context=contract.missing_context,
    )


# ----------------------------- 块构建 -----------------------------


def _title(contract: CausalContract) -> str:
    q = contract.question.strip()
    return q[:80] if q else "因果决策读出"


def _fmt_opt(x: float | None, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}g}"


def _exec_summary(
    contract: CausalContract, qa_report: CausalQAReport, readout: ExperimentReadout
) -> ReportBlock:
    body = (
        f"claim_level: {contract.claim_level.value}\n"
        f"causal_readiness: {qa_report.readiness.value} "
        f"(reporting readiness: {to_reporting_readiness(qa_report.readiness).value})\n"
        f"decision: {readout.aggregate_decision.value}\n"
        f"outcome: {readout.outcome_column} ({readout.outcome_kind.value})"
    )
    if readout.aggregate_reasons:
        body += "\nreasons: " + ", ".join(readout.aggregate_reasons)
    return ReportBlock(block_id="causal_summary", role=BlockRole.EXECUTIVE_SUMMARY, body=body)


def _data_context(readout: ExperimentReadout) -> ReportBlock:
    lines = [f"control: {readout.control_arm}  total_n: {readout.total_n}"]
    if readout.srm is not None:
        flag = "SRM detected" if readout.srm.srm_detected else "no SRM"
        lines.append(f"SRM: {flag} (chi2={_fmt_opt(readout.srm.chi_square)}, df={readout.srm.df})")
    return ReportBlock(
        block_id="causal_data_context", role=BlockRole.DATA_CONTEXT, body="\n".join(lines)
    )


def _kpi_strip(readout: ExperimentReadout) -> ReportBlock:
    cards: list[tuple[tuple[str, str], ...]] = []
    for c in readout.contrasts:
        est = c.outcome_estimate
        ci = (
            f"[{_fmt_opt(est.ci_lower)}, {_fmt_opt(est.ci_upper)}]"
            if est.ci_lower is not None
            else "n/a"
        )
        cards.append(
            (
                ("contrast", c.treatment_arm),
                ("effect", _fmt_opt(est.effect)),
                ("relative", _fmt_opt(est.relative_effect)),
                ("95% CI", ci),
                ("decision", c.decision.value),
            )
        )
    return ReportBlock(block_id="causal_kpi", role=BlockRole.KPI_STRIP, kpi_cards=tuple(cards))


def _finding(contrast: ContrastResult, idx: int, readout: ExperimentReadout) -> ReportBlock:
    est = contrast.outcome_estimate
    # 中性措辞:不使用强因果动词(导致/驱动...)。FINDING 仍带内联 caveats 以备审计。
    ci = (
        f"[{_fmt_opt(est.ci_lower)}, {_fmt_opt(est.ci_upper)}]"
        if est.ci_lower is not None
        else "n/a"
    )
    body = (
        f"{contrast.treatment_arm} vs {readout.control_arm} on {est.outcome_column} "
        f"({est.outcome_kind.value}): difference of {_fmt_opt(est.effect)}, "
        f"95% CI {ci}, relative {_fmt_opt(est.relative_effect)}. "
        f"decision: {contrast.decision.value}."
    )
    caveats: list[str] = []
    if est.degenerate:
        caveats.append("效应估计退化(SE=0/空组),不确定性不可评估")
    if "low_cell_count" in est.notes:
        caveats.append("低单元格计数,正态近似可能不稳定")
    return ReportBlock(
        block_id=f"causal_finding_{idx}",
        role=BlockRole.FINDING,
        body=body,
        caveats=tuple(caveats),
        evidence_refs=(f"contrast_{contrast.treatment_arm}",),
    )


def _contrast_caveat(contrast: ContrastResult, idx: int, readout: ExperimentReadout) -> ReportBlock:
    notes: list[str] = []
    if readout.srm is not None and readout.srm.srm_detected:
        notes.append("样本比例失衡(SRM):分流完整性存疑,不应据该对比的 lift 行动")
    if contrast.outcome_estimate.degenerate:
        notes.append("退化估计:不报告 z/p,决策为 inconclusive")
    for seg in contrast.segments:
        notes.append(f"分群 {seg.column}:Stage1 仅描述性,未做分群级检验")
    for g in contrast.guardrails:
        if g.breached:
            notes.append(f"护栏 {g.column} 破阈({g.unfavorable_direction})")
    if not notes:
        notes.append("无额外 caveat;效应为观察到的差异,因果解释依赖假设与设计")
    return ReportBlock(
        block_id=f"causal_caveat_{idx}",
        role=BlockRole.CAVEAT,
        body="; ".join(notes),
    )


def _recommendation(action_plan: ActionPlan) -> ReportBlock:
    lines = [f"decision: {action_plan.decision.value}"]
    for rec in action_plan.recommendations:
        line = f"- {rec.code}" + (f" ({rec.target_arm})" if rec.target_arm else "")
        if rec.rationale:
            line += f": {rec.rationale}"
        lines.append(line)
    for risk in action_plan.open_risks:
        lines.append(f"risk: {risk}")
    ev_refs = tuple(f"contrast_{r.target_arm}" for r in action_plan.recommendations if r.target_arm)
    if not ev_refs:
        # 兜底:ship/hold/add_power 等决策无具体 target_arm 时仍要可溯源,
        # 否则 reporting QA 触发 recommendation.no_evidence。
        ev_refs = ("causal_readout",)
    return ReportBlock(
        block_id="causal_recommendation",
        role=BlockRole.RECOMMENDATION,
        body="\n".join(lines),
        evidence_refs=ev_refs,
    )


def _assumption_caveat(contract: CausalContract, qa_report: CausalQAReport) -> ReportBlock:
    notes: list[str] = []
    for f in qa_report.findings:
        notes.append(f"[{f.severity}] {f.code}: {f.message}")
    if contract.business_assumptions:
        notes.append("假设(推断,除非用户确认): " + "; ".join(contract.business_assumptions))
    if contract.external_events:
        notes.append("外部事件: " + "; ".join(contract.external_events))
    if not notes:
        notes.append("无显式假设/外部事件声明")
    return ReportBlock(block_id="causal_assumptions", role=BlockRole.CAVEAT, body="\n".join(notes))


def _source_metadata(readout: ExperimentReadout) -> ReportBlock:
    evidence_ids = tuple(f"contrast_{c.treatment_arm}" for c in readout.contrasts)
    return ReportBlock(
        block_id="causal_sources",
        role=BlockRole.SOURCE_METADATA,
        body=f"method: welch_z_approx, contrasts: {len(readout.contrasts)}",
        evidence_refs=evidence_ids,
    )


def _data_scope(readout: ExperimentReadout) -> str:
    arms = [readout.control_arm, *(c.treatment_arm for c in readout.contrasts)]
    return f"arms: {', '.join(dict.fromkeys(arms))}; n: {readout.total_n}"
