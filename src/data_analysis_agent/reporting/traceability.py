"""报告领域层(Wave 1):可溯源映射。

把 UserNeed + DataContext(+ ProcessContext)归约为「未来契约字段」的来源链接
(TraceLink)。每个 TraceLink 解释某契约字段(report_type / time_window / metrics /
dimensions / business_grain / data_sources / audience / language)为何存在、来自何方。

**无依据的字段不产 link**(如用户未明示、数据中也无线索的 comparison)——让其随后
在 QA 阶段被 flag 为断链。这是 spec §3 硬规则的体现:"If a report section exists only
because the model 'felt like it', the contract is weak."

``ProcessContext`` 在 Wave 1 主要服务于 evidence/finding 级溯源(Wave 2 接入),此处
保留参数以便未来扩展,不强行虚构契约字段来源。
"""

from __future__ import annotations

from collections.abc import Iterable

from data_analysis_agent.reporting.model import (
    DataContext,
    ProcessContext,
    SourceKind,
    TraceLink,
    UserNeed,
)

__all__ = ["link_to_contract_fields", "explain_link", "index_by_target"]


def _explicit(target: str, source_ref: str, rationale: str) -> TraceLink:
    return TraceLink(
        target=target, source=SourceKind.EXPLICIT_USER, source_ref=source_ref, rationale=rationale
    )


def _data(target: str, source_ref: str, rationale: str) -> TraceLink:
    return TraceLink(
        target=target, source=SourceKind.DATA_CONTEXT, source_ref=source_ref, rationale=rationale
    )


def link_to_contract_fields(
    user_need: UserNeed,
    data_context: DataContext,
    process_context: ProcessContext,  # noqa: ARG001  Wave 2 evidence-level 溯源用
) -> tuple[TraceLink, ...]:
    """从 UserNeed + DataContext 推导契约字段的来源链接。"""
    er = user_need.explicit_requirements
    ir = user_need.implicit_requirements
    links: list[TraceLink] = []

    if ir.likely_report_type is not None:
        links.append(
            TraceLink(
                target="report_type",
                source=SourceKind.IMPLICIT_USER,
                source_ref="likely_report_type",
                rationale="从用户措辞推断的报告类型",
            )
        )
    if er.audience is not None:
        links.append(_explicit("audience", "audience", "用户明示的受众"))
    if er.language is not None:
        links.append(_explicit("language", "language", "请求语言判定的输出语言"))
    if er.time_window is not None:
        links.append(_explicit("time_window", "time_window", "用户明示的时间范围"))
    elif data_context.candidate_date_columns:
        links.append(
            _data(
                "time_window",
                ",".join(data_context.candidate_date_columns),
                "用户未明示时间,数据中存在日期列可推断时间范围",
            )
        )
    for metric in er.named_metrics:
        links.append(_explicit("metrics", metric, f"用户点名的指标 {metric}"))
    for col in data_context.candidate_metric_columns:
        links.append(_data("metrics", col, "数据中的候选指标列"))
    for dim in er.named_dimensions:
        links.append(_explicit("dimensions", dim, f"用户点名的维度 {dim}"))
    for col in data_context.candidate_dimensions:
        links.append(_data("dimensions", col, "数据中的候选维度列"))
    if data_context.business_grain is not None:
        links.append(_data("business_grain", data_context.business_grain, "从列名推断的业务粒度"))
    for tb in data_context.tables:
        links.append(_data("data_sources", tb.path or tb.name, "授权数据源"))

    return tuple(links)


_EXPLAIN: dict[SourceKind, str] = {
    SourceKind.EXPLICIT_USER: "用户显式陈述",
    SourceKind.IMPLICIT_USER: "用户措辞推断",
    SourceKind.DATA_CONTEXT: "数据上下文",
    SourceKind.PROCESS_CONTEXT: "分析过程",
    SourceKind.MEMORY: "领域记忆",
    SourceKind.TEMPLATE: "模板默认",
}


def explain_link(link: TraceLink) -> str:
    """TraceLink → 中文人读解释。"""
    origin = _EXPLAIN.get(link.source, "未知来源")
    return f"字段 {link.target} ← {origin}({link.source_ref})。{link.rationale}"


def index_by_target(links: Iterable[TraceLink]) -> dict[str, tuple[TraceLink, ...]]:
    """按 target 分组(同一字段可能有多个来源链接)。"""
    bucket: dict[str, list[TraceLink]] = {}
    for lk in links:
        bucket.setdefault(lk.target, []).append(lk)
    return {target: tuple(group) for target, group in bucket.items()}
