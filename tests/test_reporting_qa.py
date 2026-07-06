"""Wave 2 reporting.qa: readiness 三态 + blocker/high/medium/info 规则 + 假阳性 + contract-None 短路。"""

from __future__ import annotations

from data_analysis_agent.reporting.contract import (
    BlockRole,
    ChartFamily,
    ChartFields,
    ChartSpec,
    MetricSpec,
    ReportBlock,
    ReportContract,
    ReportDocument,
    ReportType,
    TimeWindow,
)
from data_analysis_agent.reporting.model import SourceKind
from data_analysis_agent.reporting.qa import (
    Readiness,
    Severity,
    run_qa,
)


def _codes(report_codes) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.code for f in report_codes.findings}


def _ready_doc(**overrides) -> ReportDocument:  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "title": "日报",
        "contract": ReportContract(
            question="q",
            report_type=ReportType.DAILY_KPI,
            explicit_requirement_refs=("u1",),
        ),
        "data_scope": "sales.csv,上周,100 行",
        "blocks": (
            ReportBlock(block_id="h", role=BlockRole.HEADER, heading="日报"),
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论:GMV 持平"),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="来源"),
        ),
    }
    base.update(overrides)
    return ReportDocument(**base)  # type: ignore[arg-type]


# ----------------------------- readiness 三态 -----------------------------


def test_ready_when_clean():
    report = run_qa(_ready_doc(), artifact_exists=True)
    assert report.readiness is Readiness.READY
    assert not any(f.severity is Severity.BLOCKER for f in report.findings)
    assert not any(f.severity is Severity.HIGH for f in report.findings)


def test_draft_when_artifact_missing():
    report = run_qa(_ready_doc(), artifact_exists=False)
    assert report.readiness is Readiness.DRAFT
    assert "artifact.missing" in _codes(report)


def test_needs_review_when_high_present():
    doc = _ready_doc()
    doc = ReportDocument(
        title=doc.title,
        contract=doc.contract,
        data_scope=doc.data_scope,
        blocks=doc.blocks
        + (ReportBlock(block_id="f", role=BlockRole.FINDING, body="GMV 上升 12%", heading="增长"),),
    )
    report = run_qa(doc, artifact_exists=True)
    assert "finding.no_evidence" in _codes(report)
    assert report.readiness is Readiness.NEEDS_REVIEW


# ----------------------------- blockers -----------------------------


def test_contract_none_short_circuits_and_blocks():
    doc = ReportDocument(title="x")  # contract None
    report = run_qa(doc, artifact_exists=True)
    assert report.readiness is Readiness.DRAFT
    assert "contract.no_traceability" in _codes(report)
    # 不抛异常即是 short-circuit 生效


def test_contract_refs_all_empty_blocks():
    doc = _ready_doc(contract=ReportContract(question="q"))
    report = run_qa(doc, artifact_exists=True)
    assert "contract.no_traceability" in _codes(report)


def test_executive_summary_missing_blocks():
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="h", role=BlockRole.HEADER, heading="x"),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="s"),
        )
    )
    report = run_qa(doc, artifact_exists=True)
    assert "executive_summary.missing" in _codes(report)


def test_data_scope_missing_blocks():
    doc = _ready_doc(data_scope=None)
    report = run_qa(doc, artifact_exists=True)
    assert "data_scope.missing" in _codes(report)


def test_chart_block_without_spec_blocks():
    doc = _ready_doc(
        blocks=doc_blocks_with_chart(chart=None)  # type: ignore[arg-type]
    )
    report = run_qa(doc, artifact_exists=True)
    assert "chart_block.no_spec" in _codes(report)


def doc_blocks_with_chart(chart: ChartSpec | None) -> tuple[ReportBlock, ...]:
    return (
        ReportBlock(block_id="h", role=BlockRole.HEADER, heading="x"),
        ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
        ReportBlock(
            block_id="c",
            role=BlockRole.CHART,
            chart=chart,
            evidence_refs=("e1",),
            user_need_refs=("u1",),
        ),
        ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="s"),
    )


# ----------------------------- high -----------------------------


def test_finding_no_evidence():
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(block_id="f", role=BlockRole.FINDING, body="上升 12%", heading="增长"),
        )
    )
    assert "finding.no_evidence" in _codes(run_qa(doc, artifact_exists=True))


def test_chart_no_interpretation():
    doc = _ready_doc(
        blocks=doc_blocks_with_chart(
            ChartSpec(
                family=ChartFamily.TABLE,
                interpretation=None,
                evidence_refs=("e1",),
                analytical_question="q",
            )
        )
    )
    report = run_qa(doc, artifact_exists=True)
    assert "chart.no_interpretation" in _codes(report)


def test_section_no_mapping():
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(block_id="f", role=BlockRole.FINDING, body="无数字", heading="发现"),
        )
    )
    # f 无任何 ref → section.no_mapping
    assert "section.no_mapping" in _codes(run_qa(doc, artifact_exists=True))


def test_metric_ambiguous_no_def():
    contract = ReportContract(
        question="q", explicit_requirement_refs=("u1",), metrics=(MetricSpec(name="gmv"),)
    )
    doc = _ready_doc(contract=contract)
    assert "metric.ambiguous_no_def" in _codes(run_qa(doc, artifact_exists=True))


def test_metric_inferred_drives_recommendation():
    contract = ReportContract(
        question="q",
        explicit_requirement_refs=("u1",),
        metrics=(MetricSpec(name="gmv", source=SourceKind.IMPLICIT_USER, confirmed=False),),
    )
    doc = _ready_doc(
        contract=contract,
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(
                block_id="r",
                role=BlockRole.RECOMMENDATION,
                body="加大 gmv 投入",
                evidence_refs=("e1",),
            ),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="s"),
        ),
    )
    assert "metric.inferred_drives_recommendation" in _codes(run_qa(doc, artifact_exists=True))


def test_trend_too_few_points():
    doc = _ready_doc(
        blocks=doc_blocks_with_chart(
            ChartSpec(
                family=ChartFamily.LINE,
                interpretation="i",
                evidence_refs=("e1",),
                analytical_question="q",
            )
        )
    )
    report = run_qa(doc, artifact_exists=True, n_points_by_chart={"c": 2})
    assert "trend.too_few_points" in _codes(report)


def test_trend_skipped_when_no_count_and_sufficient():
    doc = _ready_doc(
        blocks=doc_blocks_with_chart(
            ChartSpec(
                family=ChartFamily.LINE,
                interpretation="i",
                evidence_refs=("e1",),
                analytical_question="q",
            )
        )
    )
    report = run_qa(doc, artifact_exists=True)  # 未提供计数
    assert "trend.too_few_points" not in _codes(report)


def test_scatter_too_few_observations():
    doc = _ready_doc(
        blocks=doc_blocks_with_chart(
            ChartSpec(
                family=ChartFamily.SCATTER,
                interpretation="i",
                evidence_refs=("e1",),
                analytical_question="q",
            )
        )
    )
    report = run_qa(doc, artifact_exists=True, n_observations_by_chart={"c": 5})
    assert "scatter.too_few_observations" in _codes(report)


def test_recommendation_no_evidence():
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(block_id="r", role=BlockRole.RECOMMENDATION, body="建议 A"),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="s"),
        )
    )
    assert "recommendation.no_evidence" in _codes(run_qa(doc, artifact_exists=True))


def test_causal_no_caveat_strong_marker():
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(
                block_id="f",
                role=BlockRole.FINDING,
                body="渠道 A 导致 GMV 上升",
                heading="归因",
                evidence_refs=("e1",),
                user_need_refs=("u1",),
            ),
        )
    )
    assert "causal.no_caveat" in _codes(run_qa(doc, artifact_exists=True))


def test_causal_suppressed_when_next_block_is_caveat():
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(
                block_id="f",
                role=BlockRole.FINDING,
                body="渠道 A 导致 GMV 上升",
                heading="归因",
                evidence_refs=("e1",),
                user_need_refs=("u1",),
            ),
            ReportBlock(block_id="cv", role=BlockRole.CAVEAT, body="可能非唯一原因"),
        )
    )
    assert "causal.no_caveat" not in _codes(run_qa(doc, artifact_exists=True))


def test_partial_period_undisclosed():
    contract = ReportContract(
        question="q",
        explicit_requirement_refs=("u1",),
        time_window=TimeWindow(partial_period=True),
    )
    doc = _ready_doc(contract=contract)
    assert "partial_period.undisclosed" in _codes(run_qa(doc, artifact_exists=True))


def test_partial_period_disclosed_by_caveat():
    contract = ReportContract(
        question="q",
        explicit_requirement_refs=("u1",),
        time_window=TimeWindow(partial_period=True),
    )
    doc = _ready_doc(
        contract=contract,
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(block_id="cv", role=BlockRole.CAVEAT, body="本周期为部分周期"),
        ),
    )
    assert "partial_period.undisclosed" not in _codes(run_qa(doc, artifact_exists=True))


# ----------------------------- 假阳性(评审 #9) -----------------------------


def test_causal_not_fired_on_weak_marker_without_figure():
    # "因为" 在非因果(且非量化)语境 → 不触发
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(
                block_id="f",
                role=BlockRole.FINDING,
                body="用户因为预算有限才问该问题",
                heading="背景",
                evidence_refs=("e1",),
                user_need_refs=("u1",),
            ),
        )
    )
    assert "causal.no_caveat" not in _codes(run_qa(doc, artifact_exists=True))


def test_heading_generic_exact_match_only():
    # 通用词作为子串不应触发;精确等于才触发
    doc_generic = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(block_id="f", role=BlockRole.FINDING, body="无数字", heading="分析"),
        )
    )
    assert "heading.generic" in _codes(run_qa(doc_generic, artifact_exists=True))

    doc_real = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(
                block_id="f", role=BlockRole.FINDING, body="无数字", heading="关键指标分析"
            ),
        )
    )
    assert "heading.generic" not in _codes(run_qa(doc_real, artifact_exists=True))


# ----------------------------- medium / info -----------------------------


def test_chart_long_labels_medium():
    doc = _ready_doc(
        blocks=doc_blocks_with_chart(
            ChartSpec(
                family=ChartFamily.TABLE,
                interpretation="i",
                evidence_refs=("e1",),
                analytical_question="q",
                fields=ChartFields(label="x" * 25),
            )
        )
    )
    report = run_qa(doc, artifact_exists=True)
    assert "chart.long_labels" in _codes(report)


def test_repeated_chart_family_no_rationale_medium():
    chart = ChartSpec(
        family=ChartFamily.LINE,
        interpretation="i",
        evidence_refs=("e1",),
        analytical_question=None,
        data_sufficient=True,
    )
    block_list: list[ReportBlock] = [
        ReportBlock(block_id="h", role=BlockRole.HEADER, heading="x"),
        ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
    ]
    for i in range(4):
        block_list.append(
            ReportBlock(
                block_id=f"c{i}",
                role=BlockRole.CHART,
                chart=chart,
                evidence_refs=("e1",),
                user_need_refs=("u1",),
            )
        )
    doc = ReportDocument(
        title="x",
        contract=ReportContract(question="q", explicit_requirement_refs=("u1",)),
        data_scope="s",
        blocks=tuple(block_list),
    )
    report = run_qa(doc, artifact_exists=True, n_points_by_chart={f"c{i}": 10 for i in range(4)})
    assert "chart.repeated_family_no_rationale" in _codes(report)


def test_caveat_not_adjacent_medium():
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(
                block_id="f1",
                role=BlockRole.FINDING,
                body="发现一",
                heading="增长",
                evidence_refs=("e1",),
                user_need_refs=("u1",),
            ),
            ReportBlock(
                block_id="f2",
                role=BlockRole.FINDING,
                body="发现二",
                heading="回落",
                evidence_refs=("e2",),
                user_need_refs=("u1",),
            ),
            ReportBlock(block_id="cv", role=BlockRole.CAVEAT, body="综合说明"),
        )
    )
    report = run_qa(doc, artifact_exists=True)
    assert "caveat.not_adjacent" in _codes(report)


def test_info_findings_present():
    doc = _ready_doc(
        blocks=(
            ReportBlock(block_id="h", role=BlockRole.HEADER, heading="x"),
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
        )
    )
    codes = _codes(run_qa(doc, artifact_exists=True))
    assert "source_metadata.missing" in codes
    assert "print_styling.unchecked" in codes


# ----------------------------- 评审覆盖缺口补充 -----------------------------


def test_metric_inferred_deterministic_across_multiple():
    # 多个推断指标同时命中同一推荐 → 按 contract.metrics 顺序取首个(确定性,评审 High)
    contract = ReportContract(
        question="q",
        explicit_requirement_refs=("u1",),
        metrics=(
            MetricSpec(name="zzz_metric", source=SourceKind.IMPLICIT_USER, confirmed=False),
            MetricSpec(name="aaa_metric", source=SourceKind.IMPLICIT_USER, confirmed=False),
        ),
    )
    doc = _ready_doc(
        contract=contract,
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
            ReportBlock(
                block_id="r",
                role=BlockRole.RECOMMENDATION,
                body="aaa_metric 与 zzz_metric 均上涨",
                evidence_refs=("e1",),
            ),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="s"),
        ),
    )
    report = run_qa(doc, artifact_exists=True)
    finding = next(f for f in report.findings if f.code == "metric.inferred_drives_recommendation")
    # contract.metrics 中 zzz_metric 在 aaa_metric 之前 → 命中 zzz_metric(不随哈希种子变化)
    assert "zzz_metric" in finding.message


def test_chart_data_sufficient_false_without_external_count():
    doc = _ready_doc(
        blocks=doc_blocks_with_chart(
            ChartSpec(
                family=ChartFamily.LINE,
                interpretation="i",
                evidence_refs=("e1",),
                analytical_question="q",
                data_sufficient=False,
            )
        )
    )
    report = run_qa(doc, artifact_exists=True)  # 调用方未给计数
    assert "trend.too_few_points" in _codes(report)


def test_run_qa_empty_blocks_does_not_crash():
    doc = ReportDocument(
        title="x",
        contract=ReportContract(question="q", explicit_requirement_refs=("u1",)),
        data_scope="s",
        blocks=(),
    )
    report = run_qa(doc, artifact_exists=True)
    codes = _codes(report)
    assert "executive_summary.missing" in codes
    assert "direct_answer.missing" in codes
    assert report.readiness is Readiness.DRAFT


def test_report_document_and_contract_hashable():
    doc = ReportDocument(
        title="x",
        contract=ReportContract(question="q", explicit_requirement_refs=("u1",)),
        data_scope="s",
        blocks=(
            ReportBlock(
                block_id="k",
                role=BlockRole.KPI_STRIP,
                kpi_cards=((("label", "GMV"), ("value", "120")),),
            ),
        ),
    )
    assert hash(doc) is not None
    assert hash(doc.contract) is not None
