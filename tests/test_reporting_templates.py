"""Wave 6 reporting.templates: 8 模板 + role spine + 选择器 + 不变量 + 往返。"""

from __future__ import annotations

from data_analysis_agent.reporting.contract import BlockRole, ChartFamily, ReportType
from data_analysis_agent.reporting.templates import (
    TEMPLATES,
    ReportTemplate,
    match_template,
    select_template,
)

# ad_hoc 无 curated 模板(故意)
_NON_ADHOC = [rt for rt in ReportType if rt is not ReportType.AD_HOC]


def test_all_non_adhoc_report_types_have_template():
    for rt in _NON_ADHOC:
        assert rt in TEMPLATES, f"{rt.value} 缺模板"


def test_ad_hoc_has_no_template():
    assert ReportType.AD_HOC not in TEMPLATES
    assert select_template(ReportType.AD_HOC) is None


def test_each_template_starts_with_header_and_has_executive_summary():
    for rt, tpl in TEMPLATES.items():
        assert tpl.section_roles, f"{rt.value} section_roles 空"
        assert tpl.section_roles[0] is BlockRole.HEADER, f"{rt.value} 不以 HEADER 开头"
        assert BlockRole.EXECUTIVE_SUMMARY in tpl.section_roles, f"{rt.value} 缺 EXECUTIVE_SUMMARY"


def test_default_chart_families_all_valid():
    for tpl in TEMPLATES.values():
        assert tpl.default_chart_families, f"{tpl.report_type.value} 图族空"
        assert all(isinstance(f, ChartFamily) for f in tpl.default_chart_families)


def test_daily_kpi_spine():
    tpl = TEMPLATES[ReportType.DAILY_KPI]
    roles = tpl.section_roles
    assert BlockRole.KPI_STRIP in roles
    assert BlockRole.RECOMMENDATION in roles
    assert BlockRole.CAVEAT in roles
    assert "partial_period" in tpl.required_caveats


def test_risk_anomaly_caveat():
    tpl = TEMPLATES[ReportType.RISK_ANOMALY]
    assert "false_positive" in tpl.required_caveats
    assert ChartFamily.SCATTER in tpl.default_chart_families


def test_select_template():
    assert select_template(ReportType.DAILY_KPI).report_type is ReportType.DAILY_KPI
    assert select_template("weekly_kpi").report_type is ReportType.WEEKLY_KPI
    assert select_template("funnel").report_type is ReportType.FUNNEL
    assert select_template("ad_hoc") is None
    assert select_template("bogus") is None


def test_match_template_from_text():
    assert match_template("上周销售日报").report_type is ReportType.DAILY_KPI
    assert match_template("本周营销周报").report_type is ReportType.WEEKLY_KPI
    assert match_template("做个销售复盘").report_type is ReportType.DIAGNOSTIC
    assert match_template("检测支付异常").report_type is ReportType.RISK_ANOMALY
    assert match_template("看看这批数据的数据质量").report_type is ReportType.DATA_QUALITY
    assert match_template("给我个推荐方案").report_type is ReportType.RECOMMENDATION
    assert match_template("分析注册到付费的漏斗").report_type is ReportType.FUNNEL
    assert match_template("看看用户同期群留存").report_type is ReportType.COHORT


def test_match_template_none_on_no_keyword():
    """无报告关键词/歧义 → None(守卫 ReportType(None) 不抛)。"""
    assert match_template("分析下这份数据") is None
    assert match_template("hello world") is None


def test_template_roundtrip():
    for rt, tpl in TEMPLATES.items():
        rebuilt = ReportTemplate.from_dict(tpl.to_dict())
        assert rebuilt == tpl, f"{rt.value} 往返不等"


# ----------------------------- 域 overlay(迭代扩展) -----------------------------


def test_apply_overlay_adds_caveats():
    from data_analysis_agent.reporting.overlays import apply_overlay

    tpl = TEMPLATES[ReportType.DAILY_KPI]
    patched = apply_overlay(tpl, "retail")
    assert "inventory_turnover" in patched.required_caveats
    assert "inventory_turnover" not in tpl.required_caveats  # 原模板不可变


def test_apply_overlay_no_match_returns_original():
    from data_analysis_agent.reporting.overlays import apply_overlay

    tpl = TEMPLATES[ReportType.DAILY_KPI]
    assert apply_overlay(tpl, "bogus_domain") is tpl  # 无 overlay → 原对象
