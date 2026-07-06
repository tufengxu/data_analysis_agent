"""Wave 1 reporting.requirement_parser: 显式/隐式需求分离 + uncertainty + 澄清标志。"""

from __future__ import annotations

from data_analysis_agent.reporting.requirement_parser import parse_user_need


def _uncertainty_topics(need) -> tuple[str, ...]:
    return tuple(u.topic for u in need.uncertainties)


def test_daily_report_with_leadership_audience():
    need = parse_user_need("给我看看上周销售日报,要能给领导看")
    # 显式(lexical 事实)
    assert need.explicit_requirements.language == "zh-CN"
    assert need.explicit_requirements.requested_outputs == ("html_report",)
    assert need.explicit_requirements.audience == "business_stakeholder"
    # 隐式(推断)
    assert need.implicit_requirements.likely_report_type == "daily_kpi"
    assert need.implicit_requirements.cadence == "daily"
    assert need.implicit_requirements.narrative_style == "answer_first"
    assert "top_line_summary" in need.implicit_requirements.section_expectations
    assert "next_actions" in need.implicit_requirements.section_expectations
    # 上周命中时间词 → 无 time_window 不确定;无对比词 → comparison 不确定
    assert "time_window" not in _uncertainty_topics(need)
    assert "comparison" in _uncertainty_topics(need)
    assert need.clarification_needed is False


def test_weekly_report():
    need = parse_user_need("本周营销周报")
    assert need.implicit_requirements.likely_report_type == "weekly_kpi"
    assert need.implicit_requirements.cadence == "weekly"
    assert need.explicit_requirements.requested_outputs == ("html_report",)
    assert "comparison" in _uncertainty_topics(need)


def test_diagnostic_fupan():
    need = parse_user_need("做个上月销售复盘")
    assert need.implicit_requirements.likely_report_type == "diagnostic"
    assert "what_changed" in need.implicit_requirements.section_expectations


def test_funnel():
    need = parse_user_need("分析注册到付费的漏斗")
    assert need.implicit_requirements.likely_report_type == "funnel"


def test_cohort():
    need = parse_user_need("看看用户同期群留存")
    assert need.implicit_requirements.likely_report_type == "cohort"


def test_risk_anomaly():
    need = parse_user_need("检测支付异常")
    assert need.implicit_requirements.likely_report_type == "risk_anomaly"


def test_data_quality():
    need = parse_user_need("看看这批数据的数据质量")
    assert need.implicit_requirements.likely_report_type == "data_quality"


def test_leadership_data_without_report_type_does_not_force_clarification():
    # "给老板看下数据":有受众线索、无报告产物意图、无类型 → 不打断
    need = parse_user_need("给老板看下数据")
    assert need.explicit_requirements.audience == "business_stakeholder"
    assert need.implicit_requirements.narrative_style == "answer_first"
    assert need.implicit_requirements.likely_report_type is None
    assert need.clarification_needed is False
    assert "report_type" not in _uncertainty_topics(need)


def test_report_intent_but_type_unknown_clarifies():
    # "帮我做个报告":有报告意图、无类型 → 需澄清
    need = parse_user_need("帮我做个报告")
    assert need.explicit_requirements.requested_outputs == ("html_report",)
    assert need.implicit_requirements.likely_report_type is None
    assert need.clarification_needed is True
    assert "report_type" in _uncertainty_topics(need)
    rt = next(u for u in need.uncertainties if u.topic == "report_type")
    assert rt.needs_clarification is True


def test_plain_analysis_no_report_intent():
    need = parse_user_need("帮我分析一下")
    assert need.implicit_requirements.likely_report_type is None
    assert need.explicit_requirements.requested_outputs == ()
    assert need.clarification_needed is False
    # 仍记录 time_window / comparison 不确定(任何分析报告都需要)
    assert "time_window" in _uncertainty_topics(need)
    assert "comparison" in _uncertainty_topics(need)


def test_english_request_language_detection():
    need = parse_user_need("Give me a daily report of last week sales")
    assert need.explicit_requirements.language == "en-US"
    assert need.implicit_requirements.likely_report_type == "daily_kpi"


def test_english_uppercase_report_type_case_insensitive():
    # 评审 Medium:ASCII 关键词大小写不敏感
    need = parse_user_need("Give me a Daily report of last week sales")
    assert need.implicit_requirements.likely_report_type == "daily_kpi"
    assert need.explicit_requirements.language == "en-US"
    assert need.explicit_requirements.requested_outputs == ("html_report",)


def test_english_weekly_case_insensitive():
    need = parse_user_need("Weekly KPI review for stakeholders")
    assert need.implicit_requirements.likely_report_type == "weekly_kpi"
    assert need.explicit_requirements.audience == "business_stakeholder"


def test_priority_daily_over_diagnostic():
    # "销售日报复盘":日报优先于复盘
    need = parse_user_need("销售日报复盘")
    assert need.implicit_requirements.likely_report_type == "daily_kpi"
    assert need.implicit_requirements.cadence == "daily"


def test_no_time_window_flagged_when_absent():
    need = parse_user_need("做个销售日报对比")
    # 有"对比" → 无 comparison 不确定;无时间词 → time_window 不确定
    assert "comparison" not in _uncertainty_topics(need)
    assert "time_window" in _uncertainty_topics(need)
