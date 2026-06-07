"""Tests for the sampling-based compaction module.

The text/harness path is pure stdlib and always runs. The sandbox path needs
pandas and is guarded with ``pytest.importorskip``.
"""

from __future__ import annotations

import pytest

from data_analysis_agent.sampling import (
    SamplingConfig,
    compact_result,
    render_summary_dict,
    summarize_text,
)
from data_analysis_agent.sampling import text_summary as ts

SMALL_TRIGGER = SamplingConfig(trigger_chars=200, seed=0)


def _make_csv(n: int = 200) -> str:
    """category in {a,b,c}; value mostly small with a few outliers."""
    lines = ["category,value,label"]
    for i in range(n):
        cat = "abc"[i % 3]
        value = 100000 if i in (7, 99) else i % 50
        lines.append(f"{cat},{value},row{i}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# compact_result wiring
# --------------------------------------------------------------------------- #
def test_compact_result_passthrough_small():
    content = "small result, nothing to compact"
    out, was = compact_result(content, max_chars=50_000)
    assert out == content
    assert was is False


def test_compact_result_summarizes_large_csv():
    content = _make_csv(200)
    out, was = compact_result(content, max_chars=50_000, config=SMALL_TRIGGER)
    assert was is True
    assert len(out) < len(content)
    assert "采样摘要" in out
    assert "category" in out and "value" in out
    assert "勿据样本推断" in out  # mandatory sampling caveat


def test_compact_result_never_exceeds_max_chars():
    content = "x" * 20_000
    out, was = compact_result(content, max_chars=1_000, config=SMALL_TRIGGER)
    assert was is True
    assert len(out) <= 1_000


# --------------------------------------------------------------------------- #
# Table detection
# --------------------------------------------------------------------------- #
def test_detect_csv_table():
    parsed = ts.detect_table(_make_csv(10))
    assert parsed is not None
    headers, rows = parsed
    assert headers == ["category", "value", "label"]
    assert len(rows) == 10


def test_detect_markdown_table():
    md = "| name | age |\n| --- | --- |\n| alice | 30 |\n| bob | 25 |"
    parsed = ts.detect_table(md)
    assert parsed is not None
    headers, rows = parsed
    assert headers == ["name", "age"]
    assert ["alice", "30"] in rows


def test_detect_whitespace_table():
    text = "col_a    col_b    col_c\n1    2    3\n4    5    6\n7    8    9\n10    11    12"
    parsed = ts.detect_table(text)
    assert parsed is not None
    headers, _ = parsed
    assert headers == ["col_a", "col_b", "col_c"]


def test_detect_returns_none_for_prose():
    prose = "This is a paragraph.\nIt has several lines.\nNone of them are tabular."
    assert ts.detect_table(prose) is None


# --------------------------------------------------------------------------- #
# Statistics on parsed rows
# --------------------------------------------------------------------------- #
def test_numeric_stats_quantiles():
    numbers = [float(i) for i in range(100)]
    stats = ts._numeric_stats(numbers, SamplingConfig(quantiles=(0.0, 0.5, 1.0)))
    assert stats["min"] == 0.0
    assert stats["max"] == 99.0
    assert stats["mean"] == pytest.approx(49.5)
    quant = dict(stats["quantiles"])
    assert quant[0.5] == pytest.approx(49.5)


def test_summarize_table_rows_classifies_and_keeps_outliers():
    headers, rows = ts.detect_table(_make_csv(120))
    summary = ts.summarize_table_rows(headers, rows, SMALL_TRIGGER)
    kinds = {c.name: c.kind for c in summary.columns}
    assert kinds["value"] == "numeric"
    assert kinds["category"] == "categorical"
    # the two injected 100000 outliers should be surfaced
    assert any(r["value"] == "100000" for r in summary.outlier_rows)


def test_stratified_sampling_covers_all_categories():
    headers, rows = ts.detect_table(_make_csv(120))
    summary = ts.summarize_table_rows(headers, rows, SamplingConfig(max_sample_rows=12, seed=1))
    assert summary.sampling_method.startswith("stratified[category]")
    seen = {r["category"] for r in summary.sample_rows}
    assert seen == {"a", "b", "c"}


def test_reservoir_determinism():
    import random

    items = list(range(1000))
    a = ts._reservoir(items, 20, random.Random(42))
    b = ts._reservoir(items, 20, random.Random(42))
    assert a == b


# --------------------------------------------------------------------------- #
# Non-tabular digest + fallback
# --------------------------------------------------------------------------- #
def test_text_digest_for_prose():
    lines = [f"log line number {i} with id={i}" for i in range(500)]
    out = summarize_text("\n".join(lines), SMALL_TRIGGER)
    assert "文本结果摘要" in out
    assert "采样" in out


def test_head_tail_truncate_keeps_both_ends():
    content = "HEAD" + ("m" * 5000) + "TAIL"
    out = ts._head_tail_truncate(content, 500)
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "truncated" in out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_render_summary_dict_has_warning_and_columns():
    summary = {
        "n_rows": 1000,
        "n_cols": 2,
        "columns": [
            {
                "name": "value",
                "kind": "numeric",
                "count": 1000,
                "null_count": 0,
                "stats": {
                    "min": 0,
                    "max": 99,
                    "mean": 49.5,
                    "std": 28.8,
                    "quantiles": [[0.5, 49.5]],
                    "n_outliers": 2,
                },
            }
        ],
        "sample_rows": [{"value": "10"}],
        "outlier_rows": [],
        "sampling_method": "reservoir",
        "fidelity_level": "mid",
        "notes": [],
        "truncated": True,
    }
    out = render_summary_dict(summary)
    assert "勿据样本推断" in out
    assert "value" in out
    assert "p50=49.5" in out


# --------------------------------------------------------------------------- #
# Sandbox path (requires pandas)
# --------------------------------------------------------------------------- #
def test_sandbox_summarize_dataframe_exact_stats():
    pd = pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    from data_analysis_agent.sampling import sandbox_summary as ss

    df = pd.DataFrame(
        {
            "category": ["a", "b", "c"] * 40,
            "value": list(range(120)),
        }
    )
    df.loc[0, "value"] = 100000  # outlier
    summary = ss.summarize_dataframe(df, max_sample_rows=12, seed=1)

    assert summary["n_rows"] == 120
    assert summary["n_cols"] == 2
    by_name = {c["name"]: c for c in summary["columns"]}
    assert by_name["value"]["kind"] == "numeric"
    assert by_name["category"]["kind"] == "categorical"
    # exact max reflects the injected outlier
    assert by_name["value"]["stats"]["max"] == 100000
    # stratified sample covers all categories
    assert summary["sampling_method"].startswith("stratified[category]")
    seen = {r["category"] for r in summary["sample_rows"]}
    assert seen == {"a", "b", "c"}
    # outlier surfaced
    assert any(r["value"] == 100000 for r in summary["outlier_rows"])


def test_sandbox_summary_renders_with_shared_renderer():
    pd = pytest.importorskip("pandas")
    from data_analysis_agent.sampling import sandbox_summary as ss

    df = pd.DataFrame({"x": list(range(100))})
    summary = ss.summarize_dataframe(df)
    out = render_summary_dict(summary)
    assert "采样摘要" in out
    assert "勿据样本推断" in out


# --------------------------------------------------------------------------- #
# python_exec wiring
# --------------------------------------------------------------------------- #
def test_wrap_code_injects_summarizer():
    from data_analysis_agent.tools.python_exec import PythonAnalysisTool

    wrapped = PythonAnalysisTool()._wrap_code("x = 1")
    assert "def summarize_dataframe" in wrapped
    assert "def agent_summarize" in wrapped
    assert "_agent_auto_result = result" in wrapped


async def test_python_exec_small_print_unchanged():
    from data_analysis_agent.tools.python_exec import PythonAnalysisTool

    result = await PythonAnalysisTool().call({"code": "print('hello world')"})
    assert "hello world" in result.content
    assert "采样摘要" not in result.content
    assert result.is_error is False


async def test_python_exec_large_result_summarized():
    pytest.importorskip("pandas")
    from data_analysis_agent.tools.python_exec import PythonAnalysisTool

    code = (
        "import pandas as pd\n"
        "result = pd.DataFrame({'g': ['a','b','c']*70, 'v': list(range(210))})\n"
        "print(result)\n"
    )
    result = await PythonAnalysisTool().call({"code": code})
    assert result.is_error is False
    assert "采样摘要" in result.content
    assert "method=stratified" in result.content or "method=reservoir" in result.content
