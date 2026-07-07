"""域 overlay(spec §8 Wave 6 可选):按业务域微调模板的 required_caveats。

纯数据 + 纯函数,确定性。``apply_overlay(template, domain)`` 返回不可变的新模板
(仅追加域特化 caveat 主题;不改 section_roles 或图族)。
"""

from __future__ import annotations

import dataclasses

from data_analysis_agent.reporting.templates import ReportTemplate

__all__ = ["DOMAINS", "apply_overlay"]

DOMAINS = ("retail", "saas", "finance", "operations", "risk", "marketing")

# 域 → report_type → 额外 caveat 主题
_DOMAIN_OVERLAYS: dict[str, dict[str, tuple[str, ...]]] = {
    "retail": {
        "daily_kpi": ("inventory_turnover",),
        "data_quality": ("sku_completeness",),
    },
    "saas": {
        "daily_kpi": ("mrr_churn",),
        "diagnostic": ("cohort_confound",),
    },
    "finance": {
        "daily_kpi": ("currency_assumption",),
    },
    "operations": {
        "daily_kpi": ("sla_impact",),
    },
    "risk": {
        "risk_anomaly": ("regulatory_impact",),
    },
    "marketing": {
        "funnel": ("attribution_model",),
    },
}


def apply_overlay(template: ReportTemplate, domain: str) -> ReportTemplate:
    """返回应用域 overlay 后的模板(不可变 → dataclasses.replace 新建)。无 overlay 则原样返回。"""
    extra = _DOMAIN_OVERLAYS.get(domain, {}).get(template.report_type.value, ())
    if not extra:
        return template
    return dataclasses.replace(
        template,
        required_caveats=template.required_caveats + extra,
    )
