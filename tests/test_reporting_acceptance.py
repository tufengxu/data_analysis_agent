"""Wave 1-2 端到端 acceptance: parse→context→traceability→contract→document→QA 全链 + 三态。

对应 spec §8 Wave 1/2 acceptance 与计划 Task 9 的 6 个场景。
"""

from __future__ import annotations

from data_analysis_agent.reporting.chart_rules import MIN_TREND_POINTS
from data_analysis_agent.reporting.context_collector import build_data_context
from data_analysis_agent.reporting.contract import (
    Audience,
    BlockRole,
    ChartFamily,
    ChartSpec,
    MetricSpec,
    ReportBlock,
    ReportContract,
    ReportDocument,
    ReportType,
)
from data_analysis_agent.reporting.model import ProcessContext, SourceKind
from data_analysis_agent.reporting.qa import Readiness, run_qa
from data_analysis_agent.reporting.requirement_parser import parse_user_need
from data_analysis_agent.reporting.traceability import index_by_target, link_to_contract_fields

_PROFILE = {
    "kind": "file",
    "path": "/data/sales.csv",
    "format": "csv",
    "tables": [
        {
            "columns": [
                {"name": "order_date", "dtype": "datetime64"},
                {"name": "amount", "dtype": "float64"},
                {"name": "channel", "dtype": "object"},
            ],
            "n_rows_sampled": 100,
            "sampled": True,
        }
    ],
}


def test_scenario1_user_need_parse():
    need = parse_user_need("给我看看上周销售日报,要能给领导看")
    assert need.implicit_requirements.likely_report_type == "daily_kpi"
    assert need.implicit_requirements.cadence == "daily"
    assert need.explicit_requirements.audience == "business_stakeholder"


def test_scenario2_data_context_has_candidates():
    dc = build_data_context(_PROFILE)
    assert "order_date" in dc.candidate_date_columns
    assert "amount" in dc.candidate_metric_columns


def test_scenario3_traceability_links_and_gaps():
    need = parse_user_need("给我看看上周销售日报,要能给领导看")
    dc = build_data_context(_PROFILE)
    links = link_to_contract_fields(need, dc, ProcessContext())
    by_target = index_by_target(links)
    assert "report_type" in by_target
    assert "time_window" in by_target
    assert "comparison" not in by_target  # 无依据 → 不产 link


def _contract() -> ReportContract:
    return ReportContract(
        question="上周销售日报",
        report_type=ReportType.DAILY_KPI,
        audience=Audience.BUSINESS_STAKEHOLDER,
        explicit_requirement_refs=("report_type", "audience"),
        data_context_refs=("order_date", "amount"),
        metrics=(
            MetricSpec(
                name="gmv",
                source_columns=("amount",),
                aggregation="sum",
                confirmed=True,
                source=SourceKind.EXPLICIT_USER,
            ),
        ),
    )


def test_scenario4_finding_without_evidence_is_needs_review():
    doc = ReportDocument(
        title="销售日报",
        contract=_contract(),
        data_scope="sales.csv,上周,100 行",
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="GMV 环比上升"),
            ReportBlock(
                block_id="f",
                role=BlockRole.FINDING,
                body="GMV 上升 12%",
                heading="增长",
                # 故意缺 evidence_refs
            ),
        ),
    )
    report = run_qa(doc, artifact_exists=True)
    codes = {f.code for f in report.findings}
    assert "finding.no_evidence" in codes
    assert report.readiness is Readiness.NEEDS_REVIEW


def test_scenario5_same_doc_artifact_missing_is_draft():
    doc = ReportDocument(
        title="销售日报",
        contract=_contract(),
        data_scope="sales.csv,上周,100 行",
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="GMV 环比上升"),
            ReportBlock(block_id="f", role=BlockRole.FINDING, body="GMV 上升 12%", heading="增长"),
        ),
    )
    report = run_qa(doc, artifact_exists=False)
    assert report.readiness is Readiness.DRAFT
    assert "artifact.missing" in {f.code for f in report.findings}


def test_scenario6_complete_report_is_ready():
    doc = ReportDocument(
        title="2026-07-06 销售日报",
        contract=_contract(),
        data_scope="sales.csv,上周,100 行",
        generated_at="2026-07-06T22:00:00+08:00",
        blocks=(
            ReportBlock(block_id="h", role=BlockRole.HEADER, heading="销售日报"),
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="GMV 环比上升"),
            ReportBlock(
                block_id="f",
                role=BlockRole.FINDING,
                body="渠道 A 贡献增量",
                heading="渠道归因",
                evidence_refs=("e1",),
                user_need_refs=("report_type",),
            ),
            ReportBlock(
                block_id="c",
                role=BlockRole.CHART,
                chart=ChartSpec(
                    family=ChartFamily.GROUPED_BAR,
                    interpretation="渠道 A 领先",
                    analytical_question="哪个渠道贡献最大",
                    evidence_refs=("e1",),
                ),
                evidence_refs=("e1",),
                user_need_refs=("report_type",),
            ),
            ReportBlock(
                block_id="r",
                role=BlockRole.RECOMMENDATION,
                body="加大对渠道 A 投入",
                evidence_refs=("e1",),
            ),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="sales.csv"),
        ),
    )
    report = run_qa(doc, artifact_exists=True)
    assert not any(f.severity.value == "blocker" for f in report.findings)
    assert not any(f.severity.value == "high" for f in report.findings)
    assert report.readiness is Readiness.READY


# 回归:确认 chart_rules 常量与契约一致(防止后续 Wave 改 MIN 时静默漂移)
def test_min_trend_points_constant_imported():
    assert MIN_TREND_POINTS >= 3
