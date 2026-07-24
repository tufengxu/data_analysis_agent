"""Tests for MetricContractTool: read-only口径 canonicalization + validation.

Covers the nine roadmap fields, the completeness validators, the three memory
states (confirmed / unconfirmed / absent), name-drift, the signature, error
paths, and the MetricSpec round-trip (incl. the new ``exclusions`` field).
Mirrors the contract-tool test style.
"""

from __future__ import annotations

from data_analysis_agent.reporting.contract import MetricSpec
from data_analysis_agent.tools.metric_contract import MetricContractTool

# --- schema / security flags -------------------------------------------------


def test_schema_and_security_flags():
    tool = MetricContractTool()
    assert tool.name == "metric_contract"
    assert tool.is_read_only({}) is True
    assert tool.is_destructive({}) is False
    assert tool.is_concurrency_safe({}) is True


def test_validate_requires_name():
    tool = MetricContractTool()
    assert tool.validate_input({}).valid is False
    assert tool.validate_input({"name": "   "}).valid is False
    assert tool.validate_input({"name": 5}).valid is False
    assert tool.validate_input({"name": "gmv"}).valid is True


# --- canonicalization into MetricSpec ----------------------------------------


async def test_canonicalizes_fields_into_metric_spec():
    tool = MetricContractTool()
    result = await tool.call(
        {
            "name": "conversion_rate",
            "numerator": "n_converted",
            "denominator": "n_users",
            "aggregation": None,
            "filters": ["country = 'US'"],
            "exclusions": ["internal_accounts", "refunds"],
            "time_window": "2024-01",
            "grain": "day",
            "timezone": "UTC",
            "unit": "%",
        }
    )

    assert not result.is_error
    spec = MetricSpec.from_dict(result.metadata["metric_contract"]["metric"])
    assert spec.name == "conversion_rate"
    assert spec.numerator == "n_converted"
    assert spec.denominator == "n_users"
    assert spec.filters == ("country = 'US'",)
    assert spec.exclusions == ("internal_accounts", "refunds")
    assert spec.grain == "day"
    assert spec.timezone == "UTC"
    assert spec.unit == "%"


async def test_string_list_fields_strip_and_drop_empties():
    tool = MetricContractTool()
    result = await tool.call({"name": "m", "filters": ["  a ", "", "b"], "exclusions": ["  ", "c"]})
    spec = MetricSpec.from_dict(result.metadata["metric_contract"]["metric"])
    assert spec.filters == ("a", "b")
    assert spec.exclusions == ("c",)


async def test_blank_scalar_fields_become_none():
    tool = MetricContractTool()
    result = await tool.call({"name": "m", "numerator": "  ", "grain": ""})
    spec = MetricSpec.from_dict(result.metadata["metric_contract"]["metric"])
    assert spec.numerator is None
    assert spec.grain is None


# --- completeness findings ---------------------------------------------------


async def test_incomplete_metric_yields_error_finding():
    tool = MetricContractTool()
    result = await tool.call({"name": "ghost"})  # no num/den/agg
    findings = result.metadata["metric_contract"]["findings"]
    assert any(f["severity"] == "error" and f["code"] == "incomplete" for f in findings)


async def test_time_window_without_grain_warns():
    tool = MetricContractTool()
    result = await tool.call({"name": "m", "aggregation": "sum", "time_window": "2024-01"})
    codes = [f["code"] for f in result.metadata["metric_contract"]["findings"]]
    assert "missing_grain" in codes


async def test_grain_without_timezone_warns():
    tool = MetricContractTool()
    result = await tool.call({"name": "m", "aggregation": "sum", "grain": "day"})
    codes = [f["code"] for f in result.metadata["metric_contract"]["findings"]]
    assert "missing_timezone" in codes


async def test_denominator_without_numerator_warns():
    tool = MetricContractTool()
    result = await tool.call({"name": "m", "denominator": "n_users"})
    codes = [f["code"] for f in result.metadata["metric_contract"]["findings"]]
    assert "denominator_without_numerator" in codes


async def test_complete_metric_has_no_completeness_findings():
    tool = MetricContractTool()
    result = await tool.call(
        {
            "name": "arpu",
            "numerator": "revenue",
            "denominator": "n_users",
            "time_window": "2024-01",
            "grain": "month",
            "timezone": "UTC",
        }
    )
    codes = {f["code"] for f in result.metadata["metric_contract"]["findings"]}
    # complete metric + no memory → only the no_memory_definition info remains
    assert codes == {"no_memory_definition"}


# --- memory cross-check ------------------------------------------------------


async def test_memory_confirmed_sets_owner_confirmed():
    tool = MetricContractTool()
    result = await tool.call(
        {
            "name": "gmv",
            "aggregation": "sum",
            "memory_definition": {
                "key": "gmv",
                "content": "GMV = sum(order_amount)",
                "confirmed": True,
            },
        }
    )
    contract = result.metadata["metric_contract"]
    assert contract["owner_confirmed"] is True
    assert contract["memory_link"] == {
        "present": True,
        "key": "gmv",
        "confirmed": True,
        "content": "GMV = sum(order_amount)",
    }
    codes = [f["code"] for f in contract["findings"]]
    assert "confirmed_in_memory" in codes


async def test_memory_unconfirmed_warns():
    tool = MetricContractTool()
    result = await tool.call(
        {
            "name": "gmv",
            "aggregation": "sum",
            "memory_definition": {"key": "gmv", "content": "...", "confirmed": False},
        }
    )
    contract = result.metadata["metric_contract"]
    assert contract["owner_confirmed"] is False
    codes = [f["code"] for f in contract["findings"]]
    assert "unconfirmed_in_memory" in codes


async def test_no_memory_definition_info():
    tool = MetricContractTool()
    result = await tool.call({"name": "gmv", "aggregation": "sum"})
    contract = result.metadata["metric_contract"]
    assert contract["memory_link"]["present"] is False
    codes = [f["code"] for f in contract["findings"]]
    assert "no_memory_definition" in codes


async def test_memory_name_mismatch_warns():
    tool = MetricContractTool()
    result = await tool.call(
        {
            "name": "ARPU",
            "numerator": "revenue",
            "denominator": "n_users",
            "memory_definition": {"key": "arpu_usd", "content": "...", "confirmed": True},
        }
    )
    codes = [f["code"] for f in result.metadata["metric_contract"]["findings"]]
    assert "name_mismatch" in codes


async def test_memory_name_match_is_case_insensitive():
    tool = MetricContractTool()
    result = await tool.call(
        {
            "name": "ARPU",
            "numerator": "revenue",
            "memory_definition": {"key": "arpu", "content": "...", "confirmed": True},
        }
    )
    codes = [f["code"] for f in result.metadata["metric_contract"]["findings"]]
    assert "name_mismatch" not in codes


# --- signature ---------------------------------------------------------------


async def test_signature_stable_and_drift_sensitive():
    tool = MetricContractTool()
    base = await tool.call(
        {"name": "cvr", "numerator": "n_conv", "denominator": "n_users", "grain": "day"}
    )
    same = await tool.call(
        {"name": "cvr", "numerator": "n_conv", "denominator": "n_users", "grain": "day"}
    )
    drifted = await tool.call(
        {"name": "cvr", "numerator": "n_conv", "denominator": "n_sessions", "grain": "day"}
    )
    sig_base = base.metadata["metric_contract"]["signature"]
    assert same.metadata["metric_contract"]["signature"] == sig_base
    assert drifted.metadata["metric_contract"]["signature"] != sig_base


async def test_signature_normalizes_cosmetic_whitespace_and_case():
    tool = MetricContractTool()
    a = await tool.call({"name": "GMV", "aggregation": "Sum Amount"})
    b = await tool.call({"name": "gmv", "aggregation": "sum   amount"})
    assert a.metadata["metric_contract"]["signature"] == b.metadata["metric_contract"]["signature"]


# --- error path + metadata ---------------------------------------------------


async def test_empty_name_is_error():
    tool = MetricContractTool()
    result = await tool.call({"name": "  "})
    assert result.is_error
    assert "name" in result.content.lower()


async def test_metadata_structure():
    tool = MetricContractTool()
    result = await tool.call({"name": "m", "aggregation": "sum"})
    contract = result.metadata["metric_contract"]
    for key in ("metric", "owner_confirmed", "signature", "memory_link", "findings"):
        assert key in contract


def test_metric_contract_registered_and_read_only_classified():
    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.runtime import READ_ONLY_TOOLS, build_registry

    registry = build_registry(AgentConfig())
    names = {t.name for t in registry.get_all_base_tools()}
    assert "metric_contract" in names
    assert "metric_contract" in READ_ONLY_TOOLS


def test_metricspec_exclusions_round_trip():
    """Adding exclusions to MetricSpec is backward-compatible: old dicts (without
    it) still reconstruct, new dicts carry it through."""
    spec = MetricSpec(name="m", numerator="a", exclusions=("refunds",))
    d = spec.to_dict()
    assert d["exclusions"] == ["refunds"]
    rebuilt = MetricSpec.from_dict(d)
    assert rebuilt.exclusions == ("refunds",)
    # old-style dict without exclusions → default ()
    legacy = MetricSpec.from_dict({"name": "m", "numerator": "a"})
    assert legacy.exclusions == ()
