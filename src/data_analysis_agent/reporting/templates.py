"""报告模板(Wave 6):8 个报告类型的 curated section-role spine + 默认图族 + 必备 caveat。

纯数据 + 确定性选择器,无 LLM、无 I/O(spec §5.3 模板为数据 / §6 原型 / §8 Wave 6)。
每个模板以 HEADER 开头(§4.8 必备 role + §6.1 period-aware title)+ EXECUTIVE_SUMMARY 存在
(业务受众 answer-first)。模板是 section-role 骨架 + 期望图族 + caveat 主题,**非最终散文**;
各 finding 的具体语义(驱动/风险/分段等)由模型 heading 承载,description 字段记录 fold 关系。
"""

from __future__ import annotations

import dataclasses

from data_analysis_agent.reporting.contract import (
    BlockRole,
    ChartFamily,
    ReportType,
)
from data_analysis_agent.reporting.model import Serializable
from data_analysis_agent.reporting.requirement_parser import parse_user_need

__all__ = ["ReportTemplate", "TEMPLATES", "select_template", "match_template"]

# role 缩写(仅本模块可读性)
_H = BlockRole.HEADER
_ES = BlockRole.EXECUTIVE_SUMMARY
_DC = BlockRole.DATA_CONTEXT
_K = BlockRole.KPI_STRIP
_F = BlockRole.FINDING
_R = BlockRole.RECOMMENDATION
_C = BlockRole.CAVEAT
_T = BlockRole.TABLE
_SM = BlockRole.SOURCE_METADATA


@dataclasses.dataclass(frozen=True)
class ReportTemplate(Serializable):
    """一个报告原型:role 骨架 + 默认图族 + 必备 caveat 主题 + 说明。"""

    report_type: ReportType
    name: str
    section_roles: tuple[BlockRole, ...]
    default_chart_families: tuple[ChartFamily, ...]
    required_caveats: tuple[str, ...]
    description: str


TEMPLATES: dict[ReportType, ReportTemplate] = {
    ReportType.DAILY_KPI: ReportTemplate(
        report_type=ReportType.DAILY_KPI,
        name="日报 KPI 读数",
        section_roles=(_H, _ES, _K, _F, _F, _R, _C, _SM),
        default_chart_families=(
            ChartFamily.KPI_CARD,
            ChartFamily.LINE,
            ChartFamily.GROUPED_BAR,
            ChartFamily.BAR,
        ),
        required_caveats=("partial_period", "missing_data"),
        description=(
            "period-aware title + 执行摘要 + KPI strip + 增长驱动 finding + 异常/风险 finding "
            "+ 下一步行动 recommendation + caveat(部分周期/缺失)。ranked horizontal bar 用 BAR。"
        ),
    ),
    ReportType.WEEKLY_KPI: ReportTemplate(
        report_type=ReportType.WEEKLY_KPI,
        name="周报业务回顾",
        section_roles=(_H, _ES, _K, _F, _F, _R, _C, _SM),
        default_chart_families=(
            ChartFamily.KPI_CARD,
            ChartFamily.GROUPED_BAR,
            ChartFamily.BAR,
            ChartFamily.TABLE,
        ),
        required_caveats=("partial_period",),
        description=(
            "周摘要 + KPI strip + 环比 finding + 分段/驱动 finding + 后续 recommendation。"
            "wins/concerns 折进环比 finding;open-questions 折进 CAVEAT(spec §6.2)。"
        ),
    ),
    ReportType.DIAGNOSTIC: ReportTemplate(
        report_type=ReportType.DIAGNOSTIC,
        name="业务诊断备忘",
        section_roles=(_H, _ES, _F, _F, _F, _R, _C, _SM),
        default_chart_families=(ChartFamily.GROUPED_BAR, ChartFamily.BAR, ChartFamily.TABLE),
        required_caveats=("causal_limitation",),
        description=(
            "what-changed 摘要 + 已验证驱动 finding + 被排除解释 finding + 分段证据 finding。"
            "decision implication 与 next-investigation 共折进 RECOMMENDATION(spec §6.3)。"
        ),
    ),
    ReportType.DATA_QUALITY: ReportTemplate(
        report_type=ReportType.DATA_QUALITY,
        name="数据质量画像",
        section_roles=(_H, _ES, _DC, _F, _F, _F, _F, _F, _R, _SM),
        default_chart_families=(ChartFamily.BAR, ChartFamily.TABLE),
        required_caveats=(),
        description=(
            "适宜性判断 + 数据范围 + 缺失 + 重复/键唯一 + 类型/日期可解析 + 异常值 + 连接风险 "
            "+ 清理建议。spec §6.4 全 8 项覆盖。"
        ),
    ),
    ReportType.FUNNEL: ReportTemplate(
        report_type=ReportType.FUNNEL,
        name="漏斗转化报告",
        section_roles=(_H, _ES, _DC, _F, _F, _F, _R, _SM),
        default_chart_families=(ChartFamily.FUNNEL, ChartFamily.HEATMAP, ChartFamily.LINE),
        required_caveats=("denominator",),
        description=("阶段定义 + 转化/掉失 + 分段对比 + 瓶颈 + 行动。denominator 作 caveat。"),
    ),
    ReportType.COHORT: ReportTemplate(
        report_type=ReportType.COHORT,
        name="同期群留存报告",
        section_roles=(_H, _ES, _DC, _F, _F, _R, _SM),
        default_chart_families=(ChartFamily.HEATMAP, ChartFamily.LINE),
        required_caveats=("small_sample",),
        description=(
            "同期群定义 + 留存 + 分段 + 行动。bottleneck 是漏斗特性,此处有意省略(spec §6.5)。"
        ),
    ),
    ReportType.RISK_ANOMALY: ReportTemplate(
        report_type=ReportType.RISK_ANOMALY,
        name="风险/异常报告",
        section_roles=(_H, _ES, _F, _F, _F, _C, _R, _SM),
        default_chart_families=(ChartFamily.BAR, ChartFamily.SCATTER, ChartFamily.TABLE),
        required_caveats=("false_positive",),
        description=("检测规则 + 命中人群 + 严重度/集中度 + 误报 caveat + 运营跟进。"),
    ),
    # 注:recommendation 在 spec §6 无对应 archetype;由 ReportType.RECOMMENDATION 枚举构造。
    ReportType.RECOMMENDATION: ReportTemplate(
        report_type=ReportType.RECOMMENDATION,
        name="决策建议报告",
        section_roles=(_H, _ES, _F, _F, _R, _C, _SM),
        default_chart_families=(ChartFamily.BAR,),
        required_caveats=(),
        description="选项 + 预期影响 + 推荐 + caveat。constructed(非 spec §6 原型)。",
    ),
}


def select_template(report_type: ReportType | str) -> ReportTemplate | None:
    """按 ReportType 选模板(确定性);ad_hoc / 未知 → None。"""
    if not isinstance(report_type, ReportType):
        try:
            report_type = ReportType(report_type)
        except ValueError:
            return None
    return TEMPLATES.get(report_type)


def match_template(text: str) -> ReportTemplate | None:
    """从原始请求文本选模板(经 requirement_parser 的报告类型检测,无 LLM)。

    无关键词/歧义 → likely_report_type is None → 返 None(在 ReportType(...) 构造前守卫)。
    """
    need = parse_user_need(text)
    rt_str = need.implicit_requirements.likely_report_type
    if rt_str is None:
        return None
    return select_template(rt_str)
