"""报告领域层(Wave 2):确定性报告 QA。

``run_qa(document, *, artifact_exists, ...)`` 对 ReportDocument 跑确定性质量规则,
返回 ``QAReport``(readiness 三态 + findings)。无 LLM、无 I/O、无时间/随机依赖。

设计要点:
- ``run_qa`` 是唯一入口,故 spec §7「draft: QA not run」状态结构上不可能。
- ``document.contract is None`` 时,``contract.no_traceability`` blocker 触发,且所有
  读取 contract 字段的下游规则**短路**(评审 #2),不抛异常。
- 因果检测分强/弱标记:``导致/引起/造成`` 等强标记即触发;``因为/由于`` 弱标记需配合
  数字/百分比才算因果断言(避免「用户因为 X 才问 Y」类假阳性,评审 #9)。
- ``heading.generic`` 用**精确匹配**(非子串),避免误判「关键指标分析」等正当标题。
- 三条 spec §7 规则显式 defer(理由见模块底部 DEFERRED 注释与计划 Self-Review)。
"""

from __future__ import annotations

import dataclasses
import enum
import re
from collections.abc import Callable, Mapping
from typing import Any

from data_analysis_agent.reporting.chart_rules import MIN_SCATTER_POINTS, MIN_TREND_POINTS
from data_analysis_agent.reporting.contract import (
    Audience,
    BlockRole,
    ChartFamily,
    ReportContract,
    ReportDocument,
)
from data_analysis_agent.reporting.model import SourceKind

__all__ = ["Severity", "Readiness", "QAFinding", "QAReport", "run_qa"]

# DEFERRED(spec §7,Wave 1-2 不实现,理由见计划 Self-Review):
# - High #1 通用 inferred-as-explicit:Wave 3 report_contract 工具填 field_sources 后启用;
#   本 Wave 仅实现指标级子集 metric.inferred_drives_recommendation。
# - Medium #2 表 vs 可视化:Wave 5 结构化 chart_render 接入后(需数据 shape)。
# - Info #3 离线 ECharts:Wave 4 HTML v2 读取渲染器配置后(纯文档模型无法知悉)。


class Severity(str, enum.Enum):
    BLOCKER = "blocker"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


class Readiness(str, enum.Enum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"


@dataclasses.dataclass(frozen=True)
class QAFinding:
    severity: Severity
    code: str
    message: str
    block_id: str | None = None
    suggested_fix: str | None = None


@dataclasses.dataclass(frozen=True)
class QAReport:
    readiness: Readiness
    findings: tuple[QAFinding, ...]
    artifact_exists: bool


# ----------------------------- 判定基元 -----------------------------

# 启发式:任何数字或 % 即视为量化陈述。会过标("版本 2"/"3 个渠道"等),但作为 high-severity
# 倾向于过标(宁可多要证据);不匹配中文数字(一二三),与模块其余 CJK 逻辑略不一致,已知取舍。
_FIGURE_RE = re.compile(r"\d|%")
_GENERIC_HEADINGS: frozenset[str] = frozenset({"分析", "详情", "finding", "result", "数据", "内容"})
_STRONG_CAUSAL: frozenset[str] = frozenset(
    ("导致", "引起", "造成", "驱动", "拉动", "促使", "caused by", "drives", "driven by")
)
_WEAK_CAUSAL: frozenset[str] = frozenset(("因为", "由于"))
_PARTIAL_TOKENS: frozenset[str] = frozenset(("部分", "partial"))


def _has_figure(text: str) -> bool:
    return bool(_FIGURE_RE.search(text))


def _has_causal_claim(text: str) -> bool:
    return any(m in text for m in _STRONG_CAUSAL) or (
        any(m in text for m in _WEAK_CAUSAL) and _has_figure(text)
    )


# ----------------------------- 规则 -----------------------------


def _check_contract_traceability(contract: ReportContract | None) -> list[QAFinding]:
    if contract is None:
        return [
            QAFinding(
                Severity.BLOCKER,
                "contract.no_traceability",
                "Report Contract 缺失,无法溯源到用户需求与上下文",
                None,
                "先建 ReportContract 并关联需求/数据/过程来源",
            )
        ]
    refs_empty = (
        not contract.explicit_requirement_refs
        and not contract.implicit_requirement_refs
        and not contract.data_context_refs
        and not contract.process_context_refs
    )
    if refs_empty:
        return [
            QAFinding(
                Severity.BLOCKER,
                "contract.no_traceability",
                "Report Contract 与用户需求/数据/过程断链",
                None,
                "在 contract 中补充 explicit/implicit/data/process 来源引用",
            )
        ]
    return []


def _check_executive_summary(
    document: ReportDocument, contract: ReportContract | None
) -> list[QAFinding]:
    audience = contract.audience if contract is not None else Audience.BUSINESS_STAKEHOLDER
    if audience is Audience.BUSINESS_STAKEHOLDER and not any(
        b.role is BlockRole.EXECUTIVE_SUMMARY for b in document.blocks
    ):
        return [
            QAFinding(
                Severity.BLOCKER,
                "executive_summary.missing",
                "业务受众报告缺少执行摘要",
                None,
                "在开头添加执行摘要(先给结论)",
            )
        ]
    return []


def _check_direct_answer(document: ReportDocument) -> list[QAFinding]:
    content = [b for b in document.blocks if b.role is not BlockRole.HEADER]
    if not content:
        return [
            QAFinding(
                Severity.BLOCKER,
                "direct_answer.missing",
                "报告无内容块,缺少直接回答",
                None,
                "添加执行摘要或核心发现",
            )
        ]
    first = content[0]
    opens_with_visual = first.role in (BlockRole.CHART, BlockRole.TABLE, BlockRole.KPI_STRIP)
    if not (first.body or "").strip() and not opens_with_visual:
        return [
            QAFinding(
                Severity.BLOCKER,
                "direct_answer.missing",
                f"首个内容块({first.block_id})无正文回答",
                first.block_id,
                "在执行摘要中先给出直接回答",
            )
        ]
    return []


def _check_data_scope(document: ReportDocument) -> list[QAFinding]:
    if not (document.data_scope or "").strip():
        return [
            QAFinding(
                Severity.BLOCKER,
                "data_scope.missing",
                "报告未声明数据范围",
                None,
                "补充文件/sheet/时间范围/行数等数据范围",
            )
        ]
    return []


def _check_finding_evidence(document: ReportDocument) -> list[QAFinding]:
    out: list[QAFinding] = []
    for b in document.blocks:
        if b.role is not BlockRole.FINDING:
            continue
        body = b.body or ""
        if _has_figure(body) and not b.evidence_refs:
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "finding.no_evidence",
                    "量化发现缺少证据引用",
                    b.block_id,
                    "为该发现的数字补充 evidence_refs",
                )
            )
    return out


def _check_evidence_refs_nonempty(document: ReportDocument) -> list[QAFinding]:
    # An empty/whitespace evidence_ref ("") would satisfy `not b.evidence_refs`
    # (non-empty tuple) while providing no real source — a fake-evidence bypass
    # of the anti-entropy guarantee. Flag any such ref as HIGH.
    out: list[QAFinding] = []
    for b in document.blocks:
        if not b.evidence_refs:
            continue
        if any(not isinstance(r, str) or not r.strip() for r in b.evidence_refs):
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "evidence.empty_ref",
                    "证据引用含空字符串(无效溯源)",
                    b.block_id,
                    "移除空 evidence_refs 或填入真实来源 id",
                )
            )
    return out


def _check_evidence_refs_resolve(
    document: ReportDocument,
    resolver: Callable[[str], bool | None],
) -> list[QAFinding]:
    # §3.6 / audit P0-3: a ref that PURPORTS to be a real artifact/result (the
    # resolver can check it) but does not resolve is fabricated traceability —
    # the anti-entropy出口. The resolver returns True (resolved), False (fabricated),
    # or None (descriptive free text — cannot verify, do not penalize).
    out: list[QAFinding] = []
    for b in document.blocks:
        for ref in b.evidence_refs:
            if not isinstance(ref, str) or not ref.strip():
                continue  # _check_evidence_refs_nonempty handles empties
            try:
                status = resolver(ref)
            except Exception:
                status = None  # a resolver hiccup must never block the report
            if status is False:
                out.append(
                    QAFinding(
                        Severity.HIGH,
                        "evidence.unresolved",
                        f"证据引用无法解析到真实产物/result_id: {ref}",
                        b.block_id,
                        "填入真实 artifact 文件名或 result_id，或移除该 ref",
                    )
                )
    return out


def _check_chart_blocks(document: ReportDocument) -> list[QAFinding]:
    out: list[QAFinding] = []
    for b in document.blocks:
        if b.role is not BlockRole.CHART:
            continue
        if b.chart is None:
            out.append(
                QAFinding(
                    Severity.BLOCKER,
                    "chart_block.no_spec",
                    "图表块缺少 ChartSpec",
                    b.block_id,
                    "为该图表块补充 ChartSpec",
                )
            )
        elif not (b.chart.interpretation or "").strip():
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "chart.no_interpretation",
                    "图表缺少相邻解读",
                    b.block_id,
                    "补充图表的 interpretation",
                )
            )
    return out


def _check_section_mapping(document: ReportDocument) -> list[QAFinding]:
    out: list[QAFinding] = []
    for b in document.blocks:
        if b.role not in (BlockRole.FINDING, BlockRole.CHART):
            continue
        if not b.user_need_refs and not b.evidence_refs and not b.process_refs:
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "section.no_mapping",
                    "主要章节未映射到任何用户需求/证据/过程",
                    b.block_id,
                    "补充 user_need_refs/evidence_refs/process_refs",
                )
            )
    return out


def _check_metric_definitions(contract: ReportContract | None) -> list[QAFinding]:
    if contract is None:
        return []
    out: list[QAFinding] = []
    for m in contract.metrics:
        if m.numerator is None and m.denominator is None and m.aggregation is None:
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "metric.ambiguous_no_def",
                    f"指标 {m.name} 缺少口径定义(分子/分母/聚合)",
                    None,
                    "补充 numerator/denominator/aggregation",
                )
            )
    return out


def _check_metric_inferred_recommendation(
    document: ReportDocument, contract: ReportContract | None
) -> list[QAFinding]:
    # 尽力匹配:metric.name 出现在推荐 body 即视为驱动;存在假阴性(同义改写),docstring 已注明。
    # 按 contract.metrics 顺序迭代(不用 set)以保证确定性 —— 多指标同时命中时,
    # 报告哪个 metric 的消息文本不随哈希种子变化(评审 High)。
    if contract is None:
        return []
    inferred_metrics = [
        m for m in contract.metrics if m.source is not SourceKind.EXPLICIT_USER and not m.confirmed
    ]
    if not inferred_metrics:
        return []
    out: list[QAFinding] = []
    for b in document.blocks:
        if b.role is not BlockRole.RECOMMENDATION:
            continue
        body = b.body or ""
        hit = next((m.name for m in inferred_metrics if m.name and m.name in body), None)
        if hit is not None:
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "metric.inferred_drives_recommendation",
                    f"未确认的推断指标 {hit} 驱动推荐",
                    b.block_id,
                    "确认该指标口径或显式标注为假设",
                )
            )
    return out


def _check_data_sufficiency_charts(
    document: ReportDocument,
    n_points_by_chart: Mapping[str, int] | None,
    n_observations_by_chart: Mapping[str, int] | None,
) -> list[QAFinding]:
    # 仅在调用方提供计数或 ChartSpec.data_sufficient=False 时判定;否则跳过(无法判定)。
    n_points_by_chart = n_points_by_chart or {}
    n_observations_by_chart = n_observations_by_chart or {}
    out: list[QAFinding] = []
    for b in document.blocks:
        if b.role is not BlockRole.CHART or b.chart is None:
            continue
        chart = b.chart
        if chart.family is ChartFamily.LINE:
            n = n_points_by_chart.get(b.block_id)
            insufficient = (n is not None and n < MIN_TREND_POINTS) or (
                n is None and not chart.data_sufficient
            )
            if insufficient:
                out.append(
                    QAFinding(
                        Severity.HIGH,
                        "trend.too_few_points",
                        "趋势图点数不足",
                        b.block_id,
                        "增加时间点或改用分组柱/KPI 卡",
                    )
                )
        elif chart.family is ChartFamily.SCATTER:
            n = n_observations_by_chart.get(b.block_id)
            insufficient = (n is not None and n < MIN_SCATTER_POINTS) or (
                n is None and not chart.data_sufficient
            )
            if insufficient:
                out.append(
                    QAFinding(
                        Severity.HIGH,
                        "scatter.too_few_observations",
                        "散点观测数不足",
                        b.block_id,
                        "增加观测或改用表格",
                    )
                )
    return out


# chart_render 在产出的 option 顶层注入的来源标记键(见 chart_render._SOURCE_KEY)。
# 出口 QA 凭此区分「经结构化 chart_render 管线产出」与「手写裸 ECharts」。
_SOURCE_KEY = "_source"


def _check_chart_provenance(
    document: ReportDocument,
    chart_options: Mapping[str, Any] | None,
) -> list[QAFinding]:
    """P0-3 数值校验(来源标注分支):出口处对每张图做两道纯结构检查。

    不判断数值对错(无 kernel join key,见 spec),只标「无依据」:
    * ``chart.no_source`` — option 无 ``_source`` 标记,数值无来源轨迹。这是
      advisory 提示,非强保证:``_source`` 可被手写 option 伪造,且升级前旧
      chart_render 产物无此标记会被误标。真实威胁是「模型**意外**绕过 chart_render
      手写裸 ECharts」,不是对抗伪造,故可接受。
    * ``chart.shape_mismatch`` — bar/line 系列 ``series[*].data`` 长度与 ``xAxis.data``
      类别数不一致(结构性自相矛盾,非启发式);heatmap/scatter 等非「一类别一点」
      结构跳过(见 ``_check_chart_shape``)。
    两道均 HIGH(不阻断),与 evidence 系一致;chart_options 缺省/None 则整项 skip。
    """
    if chart_options is None:
        return []
    out: list[QAFinding] = []
    for b in document.blocks:
        if b.role is not BlockRole.CHART:
            continue
        option = chart_options.get(b.block_id)
        if not isinstance(option, dict):
            # option 缺失由 chart_block.no_spec / 渲染层管,这里不重复判。
            continue
        source = option.get(_SOURCE_KEY)
        if not (isinstance(source, dict) and source.get("tool") == "chart_render"):
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "chart.no_source",
                    "图表数值无来源标注(可能绕过 chart_render 手写)",
                    b.block_id,
                    "改用 chart_render 生成图表,使数值带来源标注",
                )
            )
        out.extend(_check_chart_shape(b.block_id, option))
    return out


def _check_chart_shape(block_id: str, option: dict[str, Any]) -> list[QAFinding]:
    """category 轴家族:series[*].data 长度须与 xAxis.data 类别数一致。

    只对 bar/line 系列做长度比对 —— 这两类每个 data 点对应一个类别。其余一律跳过
    (无法判定,不误报):scatter/heatmap 的 series.data 是坐标/三元组(长度=点数或
    单元格数,≠类别数),funnel 无 xAxis。waterfall/dot 也归 bar 系列(长度=类别数),
    由同一条规则覆盖;waterfall 把点包成 {value,itemStyle},dot 是纯数值,两者长度
    都=类别数,均可比。
    """
    x_axis = option.get("xAxis")
    if not isinstance(x_axis, dict) or x_axis.get("type") != "category":
        return []
    categories = x_axis.get("data")
    if not isinstance(categories, list):
        return []
    n_cat = len(categories)
    series = option.get("series")
    if not isinstance(series, list):
        return []
    out: list[QAFinding] = []
    for idx, s in enumerate(series):
        if not isinstance(s, dict):
            continue
        # 仅 bar/line 系列的 data 是「一类别一点」;其余结构(heatmap 三元组等)跳过。
        if s.get("type") not in ("bar", "line"):
            continue
        data = s.get("data")
        if not isinstance(data, list):
            continue
        if len(data) != n_cat:
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "chart.shape_mismatch",
                    f"series[{idx}] 数据点数 ({len(data)}) 与类别轴类别数 ({n_cat}) 不一致",
                    block_id,
                    "核对 chart_render 传入的 labels/series,使每条 series 长度等于类别数",
                )
            )
    return out


def _check_recommendations(document: ReportDocument) -> list[QAFinding]:
    out: list[QAFinding] = []
    for b in document.blocks:
        if b.role is not BlockRole.RECOMMENDATION:
            continue
        if not b.evidence_refs and not b.process_refs:
            out.append(
                QAFinding(
                    Severity.HIGH,
                    "recommendation.no_evidence",
                    "推荐缺少证据/过程支撑",
                    b.block_id,
                    "为推荐补充 evidence_refs 或 process_refs",
                )
            )
    return out


def _check_causal(document: ReportDocument) -> list[QAFinding]:
    out: list[QAFinding] = []
    blocks = document.blocks
    for i, b in enumerate(blocks):
        if b.role is not BlockRole.FINDING:
            continue
        if not _has_causal_claim(b.body or ""):
            continue
        next_is_caveat = i + 1 < len(blocks) and blocks[i + 1].role is BlockRole.CAVEAT
        if b.caveats or next_is_caveat:
            continue
        out.append(
            QAFinding(
                Severity.HIGH,
                "causal.no_caveat",
                "因果断言缺少相邻限定说明(caveat)",
                b.block_id,
                "为该因果结论加 caveat(可能非唯一原因/观察性数据)",
            )
        )
    return out


def _check_partial_period(
    document: ReportDocument, contract: ReportContract | None
) -> list[QAFinding]:
    # 严格读法(评审 Medium):仅在 CAVEAT 块含 部分/partial 视为已披露。spec §7 字面只说
    # "partial period is not disclosed",此处按 spec §6.1 把部分周期视为 caveat 性质,
    # 要求它出现在专用 caveat 块中(而非执行摘要里一笔带过)。更保守、倾向过标。
    if contract is None or not contract.time_window.partial_period:
        return []
    disclosed = any(
        b.role is BlockRole.CAVEAT and any(tok in (b.body or "") for tok in _PARTIAL_TOKENS)
        for b in document.blocks
    )
    if not disclosed:
        return [
            QAFinding(
                Severity.HIGH,
                "partial_period.undisclosed",
                "部分周期未在 caveat 中披露",
                None,
                "补充部分周期 caveat(数据不完整)",
            )
        ]
    return []


def _check_medium(document: ReportDocument) -> list[QAFinding]:
    out: list[QAFinding] = []
    for b in document.blocks:
        if b.role is BlockRole.FINDING and (b.heading or "").strip() in _GENERIC_HEADINGS:
            out.append(
                QAFinding(
                    Severity.MEDIUM,
                    "heading.generic",
                    "发现标题过于通用",
                    b.block_id,
                    "改为洞察性标题",
                )
            )
        if b.role is BlockRole.CHART and b.chart is not None:
            label = b.chart.fields.label
            if label and len(label) > 20:
                out.append(
                    QAFinding(
                        Severity.MEDIUM,
                        "chart.long_labels",
                        "图表标签过长",
                        b.block_id,
                        "缩短标签或换行",
                    )
                )
    # 同图族 >3 且均无 analytical_question
    no_question_by_family: dict[ChartFamily, list[bool]] = {}
    for b in document.blocks:
        if b.role is not BlockRole.CHART or b.chart is None:
            continue
        no_question_by_family.setdefault(b.chart.family, []).append(
            not (b.chart.analytical_question or "").strip()
        )
    for family, flags in no_question_by_family.items():
        if len(flags) > 3 and all(flags):
            out.append(
                QAFinding(
                    Severity.MEDIUM,
                    "chart.repeated_family_no_rationale",
                    f"图族 {family.value} 重复 {len(flags)} 次且均无分析理由",
                    None,
                    "为重复图表补充 analytical_question 或换图族",
                )
            )
    # 末尾单一 caveat 紧跟多个发现 → 提示就近限定(尽力)
    blocks = document.blocks
    if blocks and blocks[-1].role is BlockRole.CAVEAT:
        caveat_count = sum(1 for b in blocks if b.role is BlockRole.CAVEAT)
        finding_count = sum(1 for b in blocks if b.role is BlockRole.FINDING)
        if caveat_count == 1 and finding_count >= 2:
            out.append(
                QAFinding(
                    Severity.MEDIUM,
                    "caveat.not_adjacent",
                    "caveat 堆在末尾,未就近限定具体发现",
                    blocks[-1].block_id,
                    "把 caveat 移到对应发现旁",
                )
            )
    return out


def _check_info(document: ReportDocument) -> list[QAFinding]:
    out: list[QAFinding] = []
    if not any(b.role is BlockRole.SOURCE_METADATA for b in document.blocks):
        out.append(
            QAFinding(
                Severity.INFO,
                "source_metadata.missing",
                "可见报告未含来源元数据",
                None,
                "补充来源元数据块(可放附录)",
            )
        )
    out.append(
        QAFinding(
            Severity.INFO,
            "print_styling.unchecked",
            "打印/导出样式未经人工检查",
            None,
            "在桌面/移动宽度打开 HTML 人工核对",
        )
    )
    return out


def _classify(findings: list[QAFinding], artifact_exists: bool) -> Readiness:
    if not artifact_exists or any(f.severity is Severity.BLOCKER for f in findings):
        return Readiness.DRAFT
    if any(f.severity is Severity.HIGH for f in findings):
        return Readiness.NEEDS_REVIEW
    return Readiness.READY


def run_qa(
    document: ReportDocument,
    *,
    artifact_exists: bool = False,
    n_points_by_chart: Mapping[str, int] | None = None,
    n_observations_by_chart: Mapping[str, int] | None = None,
    evidence_resolver: Callable[[str], bool | None] | None = None,
    chart_options: Mapping[str, Any] | None = None,
) -> QAReport:
    """对 ReportDocument 跑确定性 QA,返回 readiness + findings(无 LLM)。"""
    contract = document.contract
    findings: list[QAFinding] = []
    findings.extend(_check_contract_traceability(contract))
    findings.extend(_check_executive_summary(document, contract))
    findings.extend(_check_direct_answer(document))
    findings.extend(_check_data_scope(document))
    findings.extend(_check_finding_evidence(document))
    findings.extend(_check_evidence_refs_nonempty(document))
    if evidence_resolver is not None:
        findings.extend(_check_evidence_refs_resolve(document, evidence_resolver))
    findings.extend(_check_chart_blocks(document))
    findings.extend(_check_section_mapping(document))
    findings.extend(_check_metric_definitions(contract))
    findings.extend(_check_metric_inferred_recommendation(document, contract))
    findings.extend(
        _check_data_sufficiency_charts(document, n_points_by_chart, n_observations_by_chart)
    )
    findings.extend(_check_chart_provenance(document, chart_options))
    findings.extend(_check_recommendations(document))
    findings.extend(_check_causal(document))
    findings.extend(_check_partial_period(document, contract))
    findings.extend(_check_medium(document))
    findings.extend(_check_info(document))
    if not artifact_exists:
        findings.append(
            QAFinding(
                Severity.BLOCKER,
                "artifact.missing",
                "报告产物缺失",
                None,
                "渲染并落盘 HTML 报告",
            )
        )
    return QAReport(
        readiness=_classify(findings, artifact_exists),
        findings=tuple(findings),
        artifact_exists=artifact_exists,
    )
