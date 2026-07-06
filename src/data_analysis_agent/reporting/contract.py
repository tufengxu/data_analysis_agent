"""报告领域层(Wave 2):契约与文档领域模型。

ReportContract / MetricSpec / EvidenceRef / ProcessRef / ChartSpec / ChartFields /
ReportBlock / ReportDocument + 封闭词表枚举(ReportType / Audience / BlockRole /
ChartFamily)。纯 stdlib,见 ADR 0009。

设计要点:
- 全部 ``@dataclasses.dataclass(frozen=True)`` + ``tuple`` 容器,继承 ``Serializable``
  获得 ``to_dict`` / ``from_dict``(Enum 经 ``.value`` 序列化、嵌套递归重建)。
- ``ChartFamily`` 定义于此(域模型拥有封闭词表),``chart_rules.py`` 反向 import。
- ``ReportContract.field_sources``:per-field 来源标注(spec §4.4),Wave 1-2 仅承载,
  Wave 3 的 ``report_contract`` 工具填充,通用 inferred-as-explicit QA 规则随后解锁。
- ``generated_at`` 由调用方注入,不调 ``datetime.now()``。
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import field

from data_analysis_agent.reporting.model import Serializable, SourceKind

__all__ = [
    "ReportType",
    "Audience",
    "BlockRole",
    "ChartFamily",
    "TimeWindow",
    "Comparison",
    "MetricSpec",
    "ReportContract",
    "EvidenceRef",
    "ProcessRef",
    "ChartFields",
    "ChartSpec",
    "ReportBlock",
    "ReportDocument",
]


class ReportType(str, enum.Enum):
    DAILY_KPI = "daily_kpi"
    WEEKLY_KPI = "weekly_kpi"
    DIAGNOSTIC = "diagnostic"
    RECOMMENDATION = "recommendation"
    DATA_QUALITY = "data_quality"
    FUNNEL = "funnel"
    COHORT = "cohort"
    RISK_ANOMALY = "risk_anomaly"
    AD_HOC = "ad_hoc"


class Audience(str, enum.Enum):
    BUSINESS_STAKEHOLDER = "business_stakeholder"
    TECHNICAL = "technical"


class BlockRole(str, enum.Enum):
    HEADER = "header"
    EXECUTIVE_SUMMARY = "executive_summary"
    KPI_STRIP = "kpi_strip"
    DATA_CONTEXT = "data_context"
    FINDING = "finding"
    CHART = "chart"
    TABLE = "table"
    RECOMMENDATION = "recommendation"
    CAVEAT = "caveat"
    SOURCE_METADATA = "source_metadata"


class ChartFamily(str, enum.Enum):
    KPI_CARD = "kpi_card"
    LINE = "line"
    BAR = "bar"
    GROUPED_BAR = "grouped_bar"
    STACKED_BAR = "stacked_bar"
    DOT = "dot"
    SCATTER = "scatter"
    HEATMAP = "heatmap"
    WATERFALL = "waterfall"
    FUNNEL = "funnel"
    TABLE = "table"


@dataclasses.dataclass(frozen=True)
class TimeWindow(Serializable):
    start: str | None = None
    end: str | None = None
    grain: str | None = None
    timezone: str | None = None
    partial_period: bool = False


@dataclasses.dataclass(frozen=True)
class Comparison(Serializable):
    # previous_period | target | plan | peer | historical_range | unavailable
    basis: str = "unavailable"
    description: str = ""


@dataclasses.dataclass(frozen=True)
class MetricSpec(Serializable):
    name: str
    source_columns: tuple[str, ...] = ()
    numerator: str | None = None
    denominator: str | None = None
    aggregation: str | None = None
    filters: tuple[str, ...] = ()
    time_window: str | None = None
    grain: str | None = None
    timezone: str | None = None
    unit: str | None = None
    confirmed: bool = False
    source: SourceKind = SourceKind.IMPLICIT_USER


@dataclasses.dataclass(frozen=True)
class ReportContract(Serializable):
    question: str
    report_type: ReportType = ReportType.AD_HOC
    audience: Audience = Audience.BUSINESS_STAKEHOLDER
    language: str = "auto"
    data_sources: tuple[str, ...] = ()
    authorized_scope: tuple[str, ...] = ()
    time_window: TimeWindow = field(default_factory=TimeWindow)
    comparison: Comparison = field(default_factory=Comparison)
    metrics: tuple[MetricSpec, ...] = ()
    dimensions: tuple[str, ...] = ()
    business_grain: str | None = None
    explicit_requirement_refs: tuple[str, ...] = ()
    implicit_requirement_refs: tuple[str, ...] = ()
    data_context_refs: tuple[str, ...] = ()
    process_context_refs: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = ("html_report",)
    known_constraints: tuple[str, ...] = ()
    missing_context: tuple[str, ...] = ()
    # per-field 来源标注(spec §4.4):Wave 3 report_contract 工具填充,启用通用
    # inferred-as-explicit QA 规则。
    field_sources: tuple[tuple[str, SourceKind], ...] = ()


@dataclasses.dataclass(frozen=True)
class EvidenceRef(Serializable):
    evidence_id: str
    tool_call_id: str | None = None
    source_table: str | None = None
    transformation: str | None = None
    process_step_id: str | None = None
    assumption_ids: tuple[str, ...] = ()
    computed_fields: tuple[str, ...] = ()
    row_count: int | None = None
    limitations: tuple[str, ...] = ()
    artifact_path: str | None = None


@dataclasses.dataclass(frozen=True)
class ProcessRef(Serializable):
    step_id: str
    note: str = ""


@dataclasses.dataclass(frozen=True)
class ChartFields(Serializable):
    x: str | None = None
    y: str | None = None
    color: str | None = None  # series
    label: str | None = None
    size: str | None = None
    time: str | None = None
    denominator: str | None = None


@dataclasses.dataclass(frozen=True)
class ChartSpec(Serializable):
    analytical_question: str | None = None
    supported_claim: str | None = None
    user_need_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    process_refs: tuple[ProcessRef, ...] = ()
    family: ChartFamily = ChartFamily.TABLE
    fields: ChartFields = field(default_factory=ChartFields)
    grain: str | None = None
    filters: tuple[str, ...] = ()
    time_window: str | None = None
    comparison_baseline: str | None = None
    units: str | None = None
    data_sufficient: bool = True
    fallback_family: ChartFamily | None = None
    title: str | None = None
    subtitle: str | None = None
    caption: str | None = None
    interpretation: str | None = None  # 相邻解读(QA 检查)
    accessibility_notes: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ReportBlock(Serializable):
    block_id: str
    role: BlockRole
    heading: str | None = None
    body: str | None = None
    chart: ChartSpec | None = None
    table_columns: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()
    # 每张 KPI 卡 = ((key, value), ...) 的 tuple;全不可变、可哈希、可往返(评审 #1)
    kpi_cards: tuple[tuple[tuple[str, str], ...], ...] = ()
    evidence_refs: tuple[str, ...] = ()
    process_refs: tuple[ProcessRef, ...] = ()
    user_need_refs: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ReportDocument(Serializable):
    title: str
    blocks: tuple[ReportBlock, ...] = ()
    contract: ReportContract | None = None
    generated_at: str | None = None  # 调用方注入,不在此调 datetime.now()
    data_scope: str | None = None
