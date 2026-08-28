"""Tests for cached-table query pushdown (D6 slicing)."""

from __future__ import annotations

import pytest

from data_analysis_agent.sampling.slicing import (
    SliceError,
    parse_filter,
    render_slice,
    slice_stored_table,
)


def _csv(n: int = 120) -> str:
    lines = ["region,product,units,price"]
    for i in range(n):
        lines.append(f"{'abc'[i % 3]},p{i % 7},{i},{i * 10 + 5}")
    return "\n".join(lines)


def test_parse_filter_shapes():
    assert parse_filter("units > 100") == ("units", ">", "100")
    assert parse_filter("region==east") == ("region", "==", "east")
    with pytest.raises(SliceError):
        parse_filter("units ~ 100")
    with pytest.raises(SliceError):
        parse_filter("no operator here")


def test_head_tail_sample_modes():
    content = _csv(120)
    head = slice_stored_table(content, result_id="r", mode="head", limit=3)
    tail = slice_stored_table(content, result_id="r", mode="tail", limit=3)
    sample = slice_stored_table(content, result_id="r", mode="sample", limit=5, seed=1)

    assert [r[2] for r in head.rows] == ["0", "1", "2"]
    assert [r[2] for r in tail.rows] == ["117", "118", "119"]
    assert len(sample.rows) == 5
    # deterministic under a fixed seed
    again = slice_stored_table(content, result_id="r", mode="sample", limit=5, seed=1)
    assert sample.rows == again.rows
    # sample preserves original row order
    indexes = [int(r[2]) for r in sample.rows]
    assert indexes == sorted(indexes)


def test_numeric_and_string_filters():
    content = _csv(120)
    filtered = slice_stored_table(
        content, result_id="r", mode="head", limit=500, filter_text="units>=110"
    )
    assert filtered.matched == 10
    assert all(int(r[2]) >= 110 for r in filtered.rows)

    by_name = slice_stored_table(
        content, result_id="r", mode="head", limit=500, filter_text="region==a"
    )
    assert by_name.matched == 40
    assert all(r[0] == "a" for r in by_name.rows)


def test_column_projection():
    content = _csv(120)
    sliced = slice_stored_table(
        content, result_id="r", mode="head", limit=2, columns=["region", "units"]
    )
    assert sliced.headers == ["region", "units"]
    assert all(len(row) == 2 for row in sliced.rows)


def test_fail_closed_errors():
    content = _csv(10)
    with pytest.raises(SliceError, match="未知列"):
        slice_stored_table(content, result_id="r", mode="head", columns=["nope"])
    with pytest.raises(SliceError, match="过滤列不存在"):
        slice_stored_table(content, result_id="r", mode="head", filter_text="nope>1")
    with pytest.raises(SliceError, match="不是可解析的表格"):
        slice_stored_table("plain prose\nno table here", result_id="r", mode="head")
    with pytest.raises(SliceError, match="mode"):
        slice_stored_table(content, result_id="r", mode="page")


def test_render_slice_header_and_cap():
    content = _csv(120)
    sliced = slice_stored_table(
        content, result_id="r-1", tool="t", mode="head", limit=2, filter_text="units<3"
    )
    text = render_slice(sliced)
    assert text.startswith("[result_id=r-1 | mode=head 2 of 3 matched / 120 rows | tool=t]")
    assert "| region | product | units | price |" in text
