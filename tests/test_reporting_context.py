"""Wave 1 reporting.context_collector: data_profile→DataContext、events→ProcessContext。"""

from __future__ import annotations

from data_analysis_agent.reporting.context_collector import (
    build_data_context,
    build_process_context,
)


def test_file_profile_classifies_columns():
    profile = {
        "kind": "file",
        "path": "/data/sales.csv",
        "format": "csv",
        "tables": [
            {
                "n_cols": 4,
                "columns": [
                    {"name": "order_date", "dtype": "datetime64"},
                    {"name": "amount", "dtype": "float64"},
                    {"name": "quantity", "dtype": "int64"},
                    {"name": "region", "dtype": "object"},
                ],
                "n_rows_sampled": 1000,
                "sampled": True,
            }
        ],
    }
    dc = build_data_context(profile)
    assert len(dc.tables) == 1
    tb = dc.tables[0]
    assert tb.sampled is True
    assert tb.n_rows_sampled == 1000
    assert tb.path == "/data/sales.csv"
    assert [c.name for c in tb.columns] == ["order_date", "amount", "quantity", "region"]
    assert dc.candidate_date_columns == ("order_date",)
    assert dc.candidate_metric_columns == ("amount", "quantity")
    assert dc.candidate_dimensions == ("region",)
    assert dc.business_grain == "order"


def test_directory_profile_uses_columns_preview():
    profile = {
        "kind": "directory",
        "path": "/data",
        "files": [
            {
                "name": "sales.csv",
                "format": "csv",
                "columns_preview": [
                    {"name": "order_date", "dtype": "datetime64"},
                    {"name": "amount", "dtype": "float64"},
                ],
            }
        ],
        "truncated": False,
        "n_total": 1,
    }
    dc = build_data_context(profile)
    assert dc.tables[0].name == "sales.csv"
    assert dc.candidate_date_columns == ("order_date",)
    assert dc.candidate_metric_columns == ("amount",)


def test_excel_profile_uses_sheet_name():
    profile = {
        "kind": "file",
        "path": "/data/book.xlsx",
        "format": "xlsx",
        "tables": [
            {"sheet": "Sheet1", "columns": [{"name": "user_id", "dtype": "int64"}]},
        ],
    }
    dc = build_data_context(profile)
    assert dc.tables[0].name == "Sheet1"
    assert dc.business_grain == "user"


def test_empty_profile():
    dc = build_data_context({})
    assert dc.tables == ()
    assert dc.candidate_date_columns == ()
    assert dc.business_grain is None


def test_process_context_events():
    events = [
        {"step_id": "s1", "tool": "data_profile", "summary": "profiled", "evidence_ids": ["e1"]},
        {
            "step_id": "s2",
            "tool": "python_analysis",
            "summary": "agg",
            "failed": True,
            "recovery": "rerun",
        },
    ]
    pc = build_process_context(events)
    assert len(pc.steps) == 2
    assert pc.steps[0].tool == "data_profile"
    assert pc.steps[0].evidence_ids == ("e1",)
    assert pc.steps[1].failed is True
    assert pc.steps[1].recovery == "rerun"
    assert pc.sensitive_mode is False


def test_process_context_synthesizes_step_id():
    events = [{"tool": "data_profile", "summary": "x"}]
    pc = build_process_context(events)
    assert pc.steps[0].step_id == "step_0"


def test_sensitive_mode_returns_empty_steps():
    events = [{"step_id": "s1", "tool": "x", "summary": "y"}]
    pc = build_process_context(events, sensitive_mode=True)
    assert pc.sensitive_mode is True
    assert pc.steps == ()


def test_process_context_skips_non_mapping_events():
    events = [{"step_id": "s1", "tool": "x", "summary": "y"}, "not-a-dict", None]  # type: ignore[list-item]
    pc = build_process_context(events)
    assert len(pc.steps) == 1
