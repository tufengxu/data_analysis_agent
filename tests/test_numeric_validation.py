"""Tests for P0-3 numeric validation (source-annotation branch).

Covers: chart_render injecting the ``_source`` provenance marker; the QA
``chart.no_source`` / ``chart.shape_mismatch`` checks (HIGH, non-blocking,
skip-on-absent); and the html_report wiring that passes ``charts`` into run_qa
so a hand-written option (no source trail) is flagged at the render boundary.
"""

from __future__ import annotations

from pathlib import Path

from data_analysis_agent.reporting.contract import (
    BlockRole,
    ChartSpec,
    ReportBlock,
    ReportContract,
    ReportDocument,
    ReportType,
)
from data_analysis_agent.reporting.qa import Readiness, Severity, run_qa
from data_analysis_agent.tools.chart_render import ChartRenderTool
from data_analysis_agent.tools.html_report import HtmlReportTool


def _chart_block(block_id: str = "c1") -> ReportBlock:
    return ReportBlock(
        block_id=block_id,
        role=BlockRole.CHART,
        body="see chart",
        chart=ChartSpec(family="bar", interpretation="trend up"),
        evidence_refs=("x.json",),
    )


def _qa_doc(*blocks: ReportBlock) -> ReportDocument:
    """Minimal READY-clean document (mirrors test_reporting_qa._ready_doc) plus extras."""
    return ReportDocument(
        title="日报",
        contract=ReportContract(
            question="q",
            report_type=ReportType.DAILY_KPI,
            explicit_requirement_refs=("u1",),
        ),
        data_scope="sales.csv,上周,100 行",
        blocks=(
            ReportBlock(block_id="h", role=BlockRole.HEADER, heading="日报"),
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论:GMV 持平"),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="来源"),
            *blocks,
        ),
    )


def _chart_doc(block_id: str = "c1") -> ReportDocument:
    return _qa_doc(_chart_block(block_id))


# --- chart_render injects _source -------------------------------------------


async def test_chart_render_injects_source_marker(tmp_path: Path):
    tool = ChartRenderTool(artifact_dir=tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "bar",
            "data": {"labels": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]},
        }
    )
    assert not result.is_error
    opt = result.metadata["chart_option"]
    assert opt["_source"] == {"tool": "chart_render", "family": "bar", "block_id": "c1"}


async def test_chart_render_injects_source_for_all_families(tmp_path: Path):
    tool = ChartRenderTool(artifact_dir=tmp_path)
    cases = {
        "line": {"labels": ["a"], "series": [{"values": [1]}]},
        "scatter": {"points": [[1, 2]]},
        "heatmap": {"x_labels": ["a"], "y_labels": ["b"], "values": [[0, 0, 5]]},
        "funnel": {"stages": [{"name": "s", "value": 3}]},
        "waterfall": {"labels": ["a"], "deltas": [4]},
    }
    for fam, data in cases.items():
        result = await tool.call({"block_id": f"b_{fam}", "family": fam, "data": data})
        assert not result.is_error, fam
        assert result.metadata["chart_option"]["_source"]["family"] == fam


# --- QA: chart.no_source -----------------------------------------------------


def test_qa_flags_handwritten_option_no_source():
    doc = _chart_doc()
    # hand-written option: no _source marker
    qa = run_qa(
        doc,
        artifact_exists=True,
        evidence_resolver=lambda r: True,
        chart_options={"c1": {"series": [{"type": "bar", "data": [1, 2]}]}},
    )
    codes = [f.code for f in qa.findings]
    assert "chart.no_source" in codes
    finding = next(f for f in qa.findings if f.code == "chart.no_source")
    assert finding.severity is Severity.HIGH


def test_qa_no_source_finding_absent_when_rendered():
    doc = _chart_doc()
    rendered = {
        "series": [{"type": "bar", "data": [1, 2]}],
        "_source": {"tool": "chart_render", "family": "bar", "block_id": "c1"},
    }
    qa = run_qa(
        doc,
        artifact_exists=True,
        evidence_resolver=lambda r: True,
        chart_options={"c1": rendered},
    )
    assert "chart.no_source" not in [f.code for f in qa.findings]


def test_qa_skips_provenance_when_chart_options_absent():
    doc = _chart_doc()
    # no chart_options passed → whole check skipped, zero false positives
    qa = run_qa(doc, artifact_exists=True, evidence_resolver=lambda r: True)
    assert "chart.no_source" not in [f.code for f in qa.findings]
    assert "chart.shape_mismatch" not in [f.code for f in qa.findings]


# --- QA: chart.shape_mismatch -----------------------------------------------


def test_qa_flags_series_category_length_mismatch():
    doc = _chart_doc()
    option = {
        "xAxis": {"type": "category", "data": ["a", "b", "c"]},
        "series": [{"type": "bar", "data": [1, 2]}],  # 2 points vs 3 categories
        "_source": {"tool": "chart_render", "family": "bar", "block_id": "c1"},
    }
    qa = run_qa(
        doc,
        artifact_exists=True,
        evidence_resolver=lambda r: True,
        chart_options={"c1": option},
    )
    assert "chart.shape_mismatch" in [f.code for f in qa.findings]


def test_qa_shape_ok_when_lengths_match():
    doc = _chart_doc()
    option = {
        "xAxis": {"type": "category", "data": ["a", "b"]},
        "series": [{"type": "bar", "data": [1, 2]}],
        "_source": {"tool": "chart_render", "family": "bar", "block_id": "c1"},
    }
    qa = run_qa(
        doc,
        artifact_exists=True,
        evidence_resolver=lambda r: True,
        chart_options={"c1": option},
    )
    assert "chart.shape_mismatch" not in [f.code for f in qa.findings]


def test_qa_shape_skips_non_category_axis():
    # scatter: xAxis is value-type, no category labels → no shape check, no FP
    doc = _chart_doc()
    option = {
        "xAxis": {"type": "value"},
        "series": [{"type": "scatter", "data": [[1, 2], [3, 4]]}],
        "_source": {"tool": "chart_render", "family": "scatter", "block_id": "c1"},
    }
    qa = run_qa(
        doc,
        artifact_exists=True,
        evidence_resolver=lambda r: True,
        chart_options={"c1": option},
    )
    assert "chart.shape_mismatch" not in [f.code for f in qa.findings]


async def test_every_chart_render_family_round_trips_qa_clean(tmp_path: Path):
    """Regression (review r1 MAJOR): every chart_render-produced option must pass
    run_qa with ZERO shape_mismatch / no_source findings. Heatmap's xAxis is
    category-type but its series.data is cell triples (length = cells, not
    categories) — this used to false-positive before the bar/line gate."""
    tool = ChartRenderTool(artifact_dir=tmp_path)
    cases = {
        "line": {"labels": ["a", "b"], "series": [{"values": [1, 2]}]},
        "bar": {"labels": ["a", "b"], "series": [{"values": [1, 2]}]},
        "grouped_bar": {
            "labels": ["a", "b"],
            "series": [{"name": "x", "values": [1, 2]}, {"name": "y", "values": [3, 4]}],
        },
        "stacked_bar": {"labels": ["a", "b", "c"], "series": [{"values": [1, 2, 3]}]},
        "dot": {"labels": ["a", "b"], "series": [{"values": [1, 2]}]},
        "waterfall": {"labels": ["p", "q"], "deltas": [5, -2]},
        "scatter": {"points": [[1, 2], [3, 4]]},
        # heatmap: 3 x-labels, 2 y-labels → 6 cells (the r1 false-positive case)
        "heatmap": {
            "x_labels": ["a", "b", "c"],
            "y_labels": ["u", "v"],
            "values": [[0, 0, 1], [0, 1, 2], [1, 0, 3], [1, 1, 4], [2, 0, 5], [2, 1, 6]],
        },
        "funnel": {"stages": [{"name": "s", "value": 3}]},
    }
    for fam, data in cases.items():
        result = await tool.call({"block_id": "c1", "family": fam, "data": data})
        assert not result.is_error, fam
        option = result.metadata["chart_option"]
        qa = run_qa(
            _chart_doc("c1"),
            artifact_exists=True,
            evidence_resolver=lambda r: True,
            chart_options={"c1": option},
        )
        codes = [f.code for f in qa.findings]
        assert "chart.shape_mismatch" not in codes, f"{fam}: {codes}"
        assert "chart.no_source" not in codes, f"{fam}: {codes}"


# --- HIGH findings never block (still NEEDS_REVIEW / renderable) ------------


def test_qa_high_findings_do_not_force_draft():
    doc = _chart_doc()
    qa = run_qa(
        doc,
        artifact_exists=True,
        evidence_resolver=lambda r: True,
        chart_options={"c1": {"series": [{"type": "bar", "data": [1, 2]}]}},
    )
    # only HIGH findings (no blocker) → NEEDS_REVIEW, not DRAFT
    assert qa.readiness is Readiness.NEEDS_REVIEW


# --- html_report wiring: charts map reaches run_qa ---------------------------


async def test_html_report_flags_handwritten_chart(tmp_path: Path):
    tool = HtmlReportTool(artifact_dir=tmp_path)
    # READY-clean base (header/exec/source) + one chart block with a hand-written
    # option (no _source) → only the provenance HIGH fires → renders w/ NEEDS_REVIEW.
    document = {
        "title": "日报",
        "contract": {
            "question": "q",
            "report_type": "daily_kpi",
            "explicit_requirement_refs": ["u1"],
        },
        "data_scope": "sales.csv,上周,100 行",
        "blocks": [
            {"block_id": "h", "role": "header", "heading": "日报"},
            {"block_id": "s", "role": "executive_summary", "body": "结论:GMV 持平"},
            {"block_id": "src", "role": "source_metadata", "body": "来源"},
            {
                "block_id": "c1",
                "role": "chart",
                "body": "see chart",
                "chart": {"family": "bar", "interpretation": "up"},
                "evidence_refs": ["x.json"],
            },
        ],
    }
    result = await tool.call(
        {"document": document, "charts": {"c1": {"series": [{"type": "bar", "data": [1, 2]}]}}}
    )
    assert not result.is_error
    html = Path(result.metadata["artifact_paths"][0]).read_text(encoding="utf-8")
    # NEEDS_REVIEW badge (HIGH findings) rendered: readiness.value is "needs_review"
    assert "needs_review" in html
