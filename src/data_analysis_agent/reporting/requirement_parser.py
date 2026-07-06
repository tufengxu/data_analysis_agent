"""报告领域层(Wave 1):确定性需求解析。

``parse_user_need(raw_request)`` 把用户请求拆成显式(lexical 可判定的事实)与隐式
(从措辞/场景推断)两类需求,并标记高影响不确定点与是否需澄清。

无 LLM、无外部依赖:纯子串匹配 + CJK 字符判定,与 ``skills/registry.py`` ADR 0006
(无共享 tokenizer)同源哲学。推断项一律进 ``ImplicitRequirements``,lexical 事实
进 ``ExplicitRequirements``——这是 anti-hallucination 的第一道闸:推断不得冒充事实。
"""

from __future__ import annotations

import re

from data_analysis_agent.reporting.model import (
    ExplicitRequirements,
    ImplicitRequirements,
    Uncertainty,
    UserNeed,
)

__all__ = ["parse_user_need"]

# 报告类型关键词表(按优先级顺序遍历,首个命中胜出;避免"销售日报复盘"等组合歧义)。
_DAILY = ("日报", "日報", "每日", "daily")
_WEEKLY = ("周报", "週報", "每周", "weekly")
_FUNNEL = ("漏斗", "funnel", "转化路径")
_COHORT = ("同期群", "留存", "cohort", "retention")
_RISK = ("异常", "異常", "风险", "風險", "anomaly", "outlier", "异动", "波动")
_DATA_QUALITY = ("数据质量", "数据体检", "data quality")
_DIAGNOSTIC = ("复盘", "復盤", "诊断", "診斷", "diagnostic", "归因")
_RECOMMENDATION = ("推荐", "建议", "怎么办", "该如何", "recommendation")

_REPORT_TYPE_TABLE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("daily_kpi", _DAILY),
    ("weekly_kpi", _WEEKLY),
    ("funnel", _FUNNEL),
    ("cohort", _COHORT),
    ("risk_anomaly", _RISK),
    ("data_quality", _DATA_QUALITY),
    ("diagnostic", _DIAGNOSTIC),
    ("recommendation", _RECOMMENDATION),
)

# 报告产物意图(显式):命中即 requested_outputs=("html_report",)
_REPORT_OUTPUT = (
    "报告",
    "報告",
    "报表",
    "日报",
    "周报",
    "月报",
    "年报",
    "H5",
    "html",
    "echarts",
    "dashboard",
    "report",
)

# 受众/叙事线索(领导/汇报 → business_stakeholder + answer_first)
_LEADERSHIP = (
    "给领导",
    "给老板",
    "给管理层",
    "给上级",
    "汇报",
    "向老板",
    "向领导",
    "stakeholder",
    "executive",
)

# 时间范围线索
_TIME_WINDOW = (
    "上周",
    "本周",
    "上月",
    "本月",
    "本季",
    "本年",
    "今天",
    "今日",
    "昨日",
    "昨天",
    "最近",
    "近一周",
    "近一月",
    "近七天",
    "近30天",
    "过去",
    "期间",
)

# 对比基线索
_COMPARISON = (
    "对比",
    "同比",
    "环比",
    "相比",
    "较上",
    "比上",
    "目标",
    "baseline",
    "较昨",
    "比昨",
    "较上周",
    "较上月",
)

_CJK_RE = re.compile(r"[一-鿿]")

_SECTION_EXPECTATIONS: dict[str, tuple[str, ...]] = {
    "daily_kpi": ("top_line_summary", "kpi_strip", "movement_drivers", "next_actions"),
    "weekly_kpi": ("weekly_summary", "kpi_strip", "wow_movement", "follow_ups"),
    "diagnostic": (
        "what_changed",
        "verified_drivers",
        "rejected_explanations",
        "next_investigation",
    ),
    "funnel": ("stage_definition", "drop_off", "bottleneck", "action"),
    "cohort": ("cohort_definition", "retention_matrix", "action"),
    "risk_anomaly": (
        "detection_rule",
        "flagged_population",
        "false_positive_caveat",
        "follow_up",
    ),
    "data_quality": ("suitability", "missingness", "duplicates", "cleanup_actions"),
    "recommendation": ("options", "expected_impact", "recommendation"),
}


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    # 大小写不敏感(ASCII 关键词如 daily/report/stakeholder 需匹配 "Daily"/"Report";
    # CJK 关键词 .lower() 为 no-op,不受影响)。评审 Medium。
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def _detect_report_type(text: str) -> str | None:
    for report_type, keywords in _REPORT_TYPE_TABLE:
        if _contains_any(text, keywords):
            return report_type
    return None


def _detect_cadence(report_type: str | None) -> str | None:
    if report_type == "daily_kpi":
        return "daily"
    if report_type == "weekly_kpi":
        return "weekly"
    return None


def _detect_language(text: str) -> str | None:
    if _CJK_RE.search(text):
        return "zh-CN"
    if any(c.isalpha() and c.isascii() for c in text):
        return "en-US"
    return None


def _collect_uncertainties(
    text: str, report_type: str | None, report_intent: bool
) -> tuple[Uncertainty, ...]:
    out: list[Uncertainty] = []
    # 仅当有报告产物意图却无法判定类型时,才视为需澄清的高影响歧义
    if report_intent and report_type is None:
        out.append(
            Uncertainty(
                topic="report_type",
                why="请求含报告产物意图,但无法判定报告类型",
                needs_clarification=True,
            )
        )
    if not _contains_any(text, _TIME_WINDOW):
        out.append(Uncertainty(topic="time_window", why="未指明时间范围"))
    if not _contains_any(text, _COMPARISON):
        out.append(Uncertainty(topic="comparison", why="未指明对比基线"))
    return tuple(out)


def parse_user_need(raw_request: str) -> UserNeed:
    """把原始请求解析为 UserNeed(显式/隐式分离 + 不确定点 + 澄清标志)。"""
    report_intent = _contains_any(raw_request, _REPORT_OUTPUT)
    report_type = _detect_report_type(raw_request)
    language = _detect_language(raw_request)
    leadership = _contains_any(raw_request, _LEADERSHIP)

    explicit = ExplicitRequirements(
        language=language,
        requested_outputs=("html_report",) if report_intent else (),
        audience="business_stakeholder" if leadership else None,
    )
    implicit = ImplicitRequirements(
        likely_report_type=report_type,
        cadence=_detect_cadence(report_type),
        narrative_style="answer_first" if leadership else None,
        section_expectations=(
            _SECTION_EXPECTATIONS.get(report_type, ()) if report_type is not None else ()
        ),
    )
    uncertainties = _collect_uncertainties(raw_request, report_type, report_intent)
    clarification_needed = report_intent and report_type is None

    return UserNeed(
        raw_request=raw_request,
        explicit_requirements=explicit,
        implicit_requirements=implicit,
        uncertainties=uncertainties,
        clarification_needed=clarification_needed,
    )
