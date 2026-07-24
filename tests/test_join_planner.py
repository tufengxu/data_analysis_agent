"""Tests for JoinPlannerTool: read-only cross-table join advisory.

Covers the five roadmap capabilities (candidate keys, uniqueness/relationship,
row-multiplication risk, null-key risk, recommended order) plus Excel
cross-sheet, error paths, and the fail-closed path policy. Mirrors
``test_data_quality.py``'s style.
"""

from __future__ import annotations

import pytest

from data_analysis_agent.tools.join_planner import JoinPlannerTool

# --- schema / security flags -------------------------------------------------


def test_schema_and_security_flags():
    tool = JoinPlannerTool()
    assert tool.name == "join_planner"
    assert tool.is_read_only({}) is True
    assert tool.is_destructive({}) is False
    assert tool.is_concurrency_safe({}) is True


def test_validate_requires_paths():
    tool = JoinPlannerTool()
    assert tool.validate_input({}).valid is False
    assert tool.validate_input({"paths": []}).valid is False
    assert tool.validate_input({"paths": "x.csv"}).valid is False  # not a list
    assert tool.validate_input({"paths": ["a.csv", ""]}).valid is False  # empty string
    assert tool.validate_input({"paths": ["a.csv", "b.csv"]}).valid is True


# --- core: candidate key + N:1 relationship + coverage -----------------------


async def test_two_csvs_detect_candidate_and_n1_relationship(tmp_path):
    orders = tmp_path / "orders.csv"
    orders.write_text("order_id,cust_id,amount\n1,1,10\n2,1,20\n3,2,30\n4,3,40\n", encoding="utf-8")
    customers = tmp_path / "customers.csv"
    customers.write_text("cust_id,name\n1,alice\n2,bob\n3,carol\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(orders), str(customers)]})

    assert not result.is_error
    plan = result.metadata["join_plan"]
    # only cust_id is shared (order_id is not in customers)
    keys = [ck["key"] for ck in plan["candidate_keys"]]
    assert keys == ["cust_id"]
    ck = plan["candidate_keys"][0]
    assert set(ck["present_in"]) == {"orders.csv", "customers.csv"}
    # pair order is sorted(present) → left=customers (unique), right=orders (not)
    pair = ck["pairs"][0]
    assert pair["left"] == "customers.csv"
    assert pair["right"] == "orders.csv"
    assert pair["relationship"] == "1:N"  # customers(1) : orders(N)
    # 1:N/N:1 does not multiply: est == orders rows
    assert pair["estimated_join_rows"] == 4
    assert pair["row_multiplication_risk"] == "none"
    # full overlap both ways
    assert pair["left_coverage"] == 1.0
    assert pair["right_coverage"] == 1.0


async def test_nn_relationship_flags_multiplication(tmp_path):
    left = tmp_path / "left.csv"
    left.write_text("k,v\n1,a\n1,b\n2,c\n", encoding="utf-8")  # k dupes
    right = tmp_path / "right.csv"
    right.write_text("k,w\n1,x\n2,y\n2,z\n", encoding="utf-8")  # k dupes
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(left), str(right)]})

    pair = result.metadata["join_plan"]["candidate_keys"][0]["pairs"][0]
    assert pair["relationship"] == "N:N"
    assert pair["row_multiplication_risk"] == "high"
    # Σ freq_l·freq_r over {1,2} = (2·1) + (1·2) = 4 > rows on either side (3)
    assert pair["estimated_join_rows"] == 4
    assert "N:N" in result.content or "multiplies" in result.content


async def test_estimated_join_rows_inner_join_semantics(tmp_path):
    # left k: [1,1,1,2]  right k: [1,2,2]  → est = (3·1) + (1·2) = 5
    left = tmp_path / "l.csv"
    left.write_text("k\n1\n1\n1\n2\n", encoding="utf-8")
    right = tmp_path / "r.csv"
    right.write_text("k\n1\n2\n2\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(left), str(right)]})

    pair = result.metadata["join_plan"]["candidate_keys"][0]["pairs"][0]
    assert pair["estimated_join_rows"] == 5


async def test_partial_value_coverage(tmp_path):
    # left keys {1,2,3}, right keys {2,3,4} → intersection {2,3}
    left = tmp_path / "l.csv"
    left.write_text("k\n1\n2\n3\n", encoding="utf-8")
    right = tmp_path / "r.csv"
    right.write_text("k\n2\n3\n4\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(left), str(right)]})

    pair = result.metadata["join_plan"]["candidate_keys"][0]["pairs"][0]
    assert pair["overlap_count"] == 2
    assert pair["left_coverage"] == round(2 / 3, 6)
    assert pair["right_coverage"] == round(2 / 3, 6)
    # low overlap (<50% on a side would warn; here 66%, no overlap warning) —
    # assert no false overlap warning
    assert all("low value overlap" not in w for w in result.metadata["join_plan"]["warnings"])


async def test_low_overlap_same_name_warns(tmp_path):
    # same column name, disjoint values → 0% coverage → "may not be a real key"
    left = tmp_path / "l.csv"
    left.write_text("code\n1\n2\n3\n", encoding="utf-8")
    right = tmp_path / "r.csv"
    right.write_text("code\n4\n5\n6\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(left), str(right)]})

    warnings = result.metadata["join_plan"]["warnings"]
    assert any("low value overlap" in w for w in warnings)


# --- null-key risk -----------------------------------------------------------


async def test_null_key_warning_when_majority_null(tmp_path):
    # orders.cust_id dense; customers.cust_id ≥50% null
    orders = tmp_path / "orders.csv"
    orders.write_text("cust_id,amt\n1,10\n2,20\n3,30\n", encoding="utf-8")
    customers = tmp_path / "customers.csv"
    customers.write_text("cust_id,name\n,alice\n,bob\n1,carol\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(orders), str(customers)]})

    plan = result.metadata["join_plan"]
    ck = plan["candidate_keys"][0]
    # per-table null count surfaces for the sparse table
    cust_stats = ck["per_table"]["customers.csv"]
    assert cust_stats["n_null"] == 2
    assert any("null" in w and "customers.csv" in w for w in plan["warnings"])


# --- Excel cross-sheet -------------------------------------------------------


async def test_excel_workbook_cross_sheet_candidates(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    xlsx = tmp_path / "book.xlsx"
    with pd.ExcelWriter(xlsx) as writer:
        pd.DataFrame({"order_id": [1, 2], "cust_id": [1, 1]}).to_excel(
            writer, sheet_name="orders", index=False
        )
        pd.DataFrame({"cust_id": [1, 2], "name": ["a", "b"]}).to_excel(
            writer, sheet_name="customers", index=False
        )
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(xlsx)]})

    plan = result.metadata["join_plan"]
    table_names = [t["name"] for t in plan["tables"]]
    assert {n.split("::")[1] for n in table_names} == {"orders", "customers"}
    assert [ck["key"] for ck in plan["candidate_keys"]] == ["cust_id"]
    # absolute source path reported
    assert str(xlsx.resolve()) == plan["tables"][0]["source"]


# --- recommended order --------------------------------------------------------


async def test_recommended_order_anchors_at_largest_table(tmp_path):
    big = tmp_path / "big.csv"  # 4 rows → base
    big.write_text("id,v\n1,a\n2,b\n3,c\n4,d\n", encoding="utf-8")
    small = tmp_path / "small.csv"
    small.write_text("id,w\n1,x\n2,y\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(big), str(small)]})

    plan = result.metadata["join_plan"]
    assert plan["recommended_order"][0] == "big.csv"
    steps = plan["join_steps"]
    assert steps[0]["table"] == "big.csv"
    assert "base" in steps[0]["note"]
    # small joins via id; small.id is unique → incoming unique → risk none
    step1 = steps[1]
    assert step1["via_key"] == "id"
    assert step1["risk"] == "none"


async def test_recommended_order_non_unique_incoming_is_high_risk(tmp_path):
    # fact (unique id) joined by a table whose key is NON-unique → multiplication
    fact = tmp_path / "fact.csv"
    fact.write_text("id,v\n1,a\n2,b\n3,c\n", encoding="utf-8")
    dupes = tmp_path / "dupes.csv"  # id repeats
    dupes.write_text("id,w\n1,x\n1,y\n2,z\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(fact), str(dupes)]})

    # dupes is the smaller side but its key is non-unique → joining it in multiplies
    step = result.metadata["join_plan"]["join_steps"][1]
    assert step["risk"] == "high"


# --- no candidates / structure ----------------------------------------------


async def test_no_shared_columns_warns(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("x,y\n1,2\n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text("p,q\n3,4\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(a), str(b)]})

    plan = result.metadata["join_plan"]
    assert plan["candidate_keys"] == []
    assert any("no shared-name candidate keys" in w for w in plan["warnings"])


async def test_metadata_structure(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("k,v\n1,2\n3,4\n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text("k,w\n1,5\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(a), str(b)]})

    plan = result.metadata["join_plan"]
    for key in ("tables", "candidate_keys", "recommended_order", "join_steps", "warnings"):
        assert key in plan
    t0 = plan["tables"][0]
    for key in ("name", "source", "format", "sheet", "n_rows", "n_cols", "n_truncated", "columns"):
        assert key in t0


async def test_metadata_is_json_serializable(tmp_path):
    """metadata must be JSON-serializable (trajectory persistence depends on it)."""
    import json

    a = tmp_path / "a.csv"
    a.write_text("k,v\n1,2\n3,4\n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text("k,w\n1,5\n3,6\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(a), str(b)]})

    # must not raise (no DataFrame / Timestamp / numpy type leaked into metadata)
    serialized = json.dumps(result.metadata["join_plan"])
    assert "candidate_keys" in serialized


async def test_cross_type_int_vs_str_key_does_not_silently_match(tmp_path):
    """Same-name key read as int in one table, str in the other (a non-numeric
    forces str dtype) → sets don't intersect (1 != "1") → overlap 0 + low-overlap
    warning, NOT a silent false match."""
    ints = tmp_path / "ints.csv"
    ints.write_text("k\n1\n2\n3\n", encoding="utf-8")  # int64
    strs = tmp_path / "strs.csv"
    strs.write_text("k\n1\n2\nabc\n", encoding="utf-8")  # "abc" forces str dtype
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(ints), str(strs)]})

    pair = result.metadata["join_plan"]["candidate_keys"][0]["pairs"][0]
    assert pair["overlap_count"] == 0
    assert any("low value overlap" in w for w in result.metadata["join_plan"]["warnings"])


async def test_truncated_table_sets_flag_and_warns(tmp_path, monkeypatch):
    from data_analysis_agent.tools import join_planner as jp

    monkeypatch.setattr(jp, "_MAX_ROWS", 3)
    big = tmp_path / "big.csv"
    big.write_text("k,v\n1,a\n2,b\n3,c\n4,d\n5,e\n", encoding="utf-8")
    other = tmp_path / "other.csv"
    other.write_text("k,w\n1,x\n2,y\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(big), str(other)]})

    plan = result.metadata["join_plan"]
    big_t = next(t for t in plan["tables"] if t["name"] == "big.csv")
    assert big_t["n_truncated"] is True
    assert big_t["n_rows"] == 3  # capped
    assert any("row cap" in w and "provisional" in w for w in plan["warnings"])


async def test_high_cardinality_key_skips_overlap(tmp_path, monkeypatch):
    from data_analysis_agent.tools import join_planner as jp

    monkeypatch.setattr(jp, "_MAX_OVERLAP_VALUES", 3)
    # >3 distinct values each side → overlap skipped, relationship still reported
    a = tmp_path / "a.csv"
    a.write_text("k\n1\n2\n3\n4\n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text("k\n1\n2\n3\n5\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(a), str(b)]})

    pair = result.metadata["join_plan"]["candidate_keys"][0]["pairs"][0]
    assert pair["overlap_skipped"] == "high-cardinality"
    assert pair["overlap_count"] is None
    assert pair["estimated_join_rows"] is None
    # relationship still derives from uniqueness alone
    assert pair["relationship"] == "1:1"


async def test_corrupt_file_returns_clear_error(tmp_path):
    """A malformed CSV must surface a per-file error, not bubble up generic."""
    a = tmp_path / "good.csv"
    a.write_text("k,v\n1,2\n3,4\n", encoding="utf-8")
    bad = tmp_path / "bad.xlsx"  # not a real xlsx → openpyxl/pandas will error
    bad.write_bytes(b"definitely not an xlsx file")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])

    result = await tool.call({"paths": [str(a), str(bad)]})

    assert result.is_error
    assert "Error reading" in result.content
    assert "bad.xlsx" in result.content


# --- error paths ------------------------------------------------------------


async def test_single_table_is_error(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("k,v\n1,2\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])
    result = await tool.call({"paths": [str(a)]})
    assert result.is_error
    assert "≥2 tables" in result.content


async def test_path_outside_allowed_is_error(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "secret.csv"
    outside.write_text("k,v\n1,2\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[allowed])
    result = await tool.call({"paths": [str(outside), str(outside)]})
    assert result.is_error
    assert "outside" in result.content.lower()


async def test_missing_path_is_error(tmp_path):
    tool = JoinPlannerTool(allowed_paths=[tmp_path])
    result = await tool.call({"paths": [str(tmp_path / "nope.csv"), str(tmp_path / "x.csv")]})
    assert result.is_error


async def test_unsupported_file_type_is_error(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n")
    a = tmp_path / "a.csv"
    a.write_text("k\n1\n", encoding="utf-8")
    tool = JoinPlannerTool(allowed_paths=[tmp_path])
    result = await tool.call({"paths": [str(img), str(a)]})
    assert result.is_error
    assert "unsupported" in result.content.lower()


# --- registration guard ------------------------------------------------------


def test_join_planner_registered_and_read_only_classified():
    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.runtime import READ_ONLY_TOOLS, build_registry

    registry = build_registry(AgentConfig())
    names = {t.name for t in registry.get_all_base_tools()}
    assert "join_planner" in names
    assert "join_planner" in READ_ONLY_TOOLS
