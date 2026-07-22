"""Tests for domain overlay wiring (audit reporting-side, Slice 2).

The overlay machinery (reporting.overlays.apply_overlay) was dead code; this
verifies it is now live via report_contract(domain=...) and that the domain
flows onto ReportContract + the template's required_caveats.
"""

from __future__ import annotations

from data_analysis_agent.reporting.contract import ReportContract
from data_analysis_agent.tools.report_contract import ReportContractTool


async def test_domain_applies_overlay_caveats():
    tool = ReportContractTool()
    result = await tool.call(
        {"question": "daily revenue", "report_type": "daily_kpi", "domain": "saas"}
    )
    assert not result.is_error
    contract = ReportContract.from_dict(result.metadata["contract"])
    assert contract.domain == "saas"
    template = result.metadata["template"]
    # saas daily_kpi overlay adds mrr_churn (see reporting/overlays.py)
    assert "mrr_churn" in template["required_caveats"]


async def test_finance_daily_kpi_overlay():
    tool = ReportContractTool()
    result = await tool.call(
        {"question": "daily revenue", "report_type": "daily_kpi", "domain": "finance"}
    )
    template = result.metadata["template"]
    assert "currency_assumption" in template["required_caveats"]


async def test_unknown_domain_is_noop_not_error():
    tool = ReportContractTool()
    result = await tool.call({"question": "x", "report_type": "daily_kpi", "domain": "games"})
    assert not result.is_error
    contract = ReportContract.from_dict(result.metadata["contract"])
    assert contract.domain == "games"  # still recorded
    # template has its base caveats only — no overlay for "games"
    template = result.metadata["template"]
    assert "mrr_churn" not in template["required_caveats"]


async def test_no_domain_leaves_template_unmodified():
    tool = ReportContractTool()
    with_domain = await tool.call(
        {"question": "x", "report_type": "data_quality", "domain": "retail"}
    )
    without = await tool.call({"question": "x", "report_type": "data_quality"})
    # retail data_quality overlay adds sku_completeness
    assert "sku_completeness" in with_domain.metadata["template"]["required_caveats"]
    assert "sku_completeness" not in without.metadata["template"]["required_caveats"]


async def test_ad_hoc_report_type_with_domain_does_not_crash():
    # AD_HOC / unknown report_type -> select_template returns None; domain must
    # not trigger apply_overlay on None.
    tool = ReportContractTool()
    result = await tool.call({"question": "ad hoc thing", "domain": "saas"})
    assert not result.is_error
    assert "template" not in result.metadata  # AD_HOC has no template
    contract = ReportContract.from_dict(result.metadata["contract"])
    assert contract.domain == "saas"


def test_domain_field_round_trips():
    """ReportContract.domain is additive + backward-compatible."""
    c = ReportContract(question="q", domain="retail")
    d = c.to_dict()
    assert d["domain"] == "retail"
    assert ReportContract.from_dict(d).domain == "retail"
    # legacy dict without domain -> default None
    assert ReportContract.from_dict({"question": "q"}).domain is None


def test_as_optional_str_coercion():
    from data_analysis_agent.tools.report_contract import _as_optional_str

    assert _as_optional_str("saas") == "saas"
    assert _as_optional_str("  SAAS  ") == "SAAS"  # strips; lower() happens at call site
    assert _as_optional_str("") is None
    assert _as_optional_str("   ") is None
    assert _as_optional_str(None) is None
    assert _as_optional_str(5) is None


def test_apply_overlay_is_no_longer_dead_code():
    """report_contract must be a live CALLER of apply_overlay (audit: was dead).
    Asserts the call site, not just the import line."""
    import inspect

    from data_analysis_agent.tools import report_contract as rc

    assert "apply_overlay(template" in inspect.getsource(rc)


async def test_domain_is_case_insensitive():
    tool = ReportContractTool()
    result = await tool.call(
        {"question": "daily revenue", "report_type": "daily_kpi", "domain": "SAAS"}
    )
    # normalized to lowercase → hits the saas overlay
    contract = ReportContract.from_dict(result.metadata["contract"])
    assert contract.domain == "saas"
    assert "mrr_churn" in result.metadata["template"]["required_caveats"]
