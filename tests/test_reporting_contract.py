"""Wave 2 reporting.contract: 构造、默认值、JSON 往返(含 enum、嵌套 tuple、field_sources)。"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from data_analysis_agent.reporting.contract import (
    Audience,
    BlockRole,
    ChartFamily,
    ChartFields,
    ChartSpec,
    Comparison,
    EvidenceRef,
    MetricSpec,
    ProcessRef,
    ReportBlock,
    ReportContract,
    ReportDocument,
    ReportType,
    TimeWindow,
)
from data_analysis_agent.reporting.model import SourceKind


def test_contract_defaults():
    c = ReportContract(question="上周销售情况?")
    assert c.report_type is ReportType.AD_HOC
    assert c.audience is Audience.BUSINESS_STAKEHOLDER
    assert c.language == "auto"
    assert c.required_outputs == ("html_report",)
    assert c.comparison.basis == "unavailable"
    assert c.time_window.partial_period is False


def test_metric_spec_default_source_implicit():
    m = MetricSpec(name="gmv")
    assert m.source is SourceKind.IMPLICIT_USER
    assert m.confirmed is False


def test_chart_spec_default_family_table():
    s = ChartSpec()
    assert s.family is ChartFamily.TABLE
    assert s.interpretation is None
    assert s.fields.x is None


# ---- 往返 ----


@pytest.fixture
def rich_document() -> ReportDocument:
    contract = ReportContract(
        question="上周销售日报",
        report_type=ReportType.DAILY_KPI,
        audience=Audience.BUSINESS_STAKEHOLDER,
        time_window=TimeWindow(start="2026-06-30", end="2026-07-06", partial_period=True),
        comparison=Comparison(basis="previous_period", description="环比上周"),
        metrics=(
            MetricSpec(
                name="gmv",
                source_columns=("amount",),
                aggregation="sum",
                confirmed=True,
                source=SourceKind.EXPLICIT_USER,
            ),
        ),
        field_sources=(
            ("report_type", SourceKind.IMPLICIT_USER),
            ("audience", SourceKind.EXPLICIT_USER),
        ),
    )
    return ReportDocument(
        title="2026-07-06 销售日报",
        contract=contract,
        data_scope="sales.csv,2026-06-30 至 2026-07-06,100 行",
        generated_at="2026-07-06T22:00:00+08:00",
        blocks=(
            ReportBlock(block_id="b1", role=BlockRole.HEADER, heading="销售日报"),
            ReportBlock(
                block_id="b2",
                role=BlockRole.EXECUTIVE_SUMMARY,
                body="GMV 环比上升 12%",
            ),
            ReportBlock(
                block_id="b3",
                role=BlockRole.KPI_STRIP,
                kpi_cards=(
                    (("label", "GMV"), ("value", "￥120,000"), ("delta", "+12%")),
                    (("label", "订单"), ("value", "1,200")),
                ),
            ),
            ReportBlock(
                block_id="b4",
                role=BlockRole.FINDING,
                heading="分渠道",
                body="渠道 A 贡献增量 60%",
                evidence_refs=("e1",),
                user_need_refs=("report_type",),
                chart=ChartSpec(
                    family=ChartFamily.GROUPED_BAR,
                    fields=ChartFields(x="channel", y="gmv"),
                    interpretation="渠道 A 显著领先",
                    supported_claim="渠道 A 贡献增量 60%",
                    evidence_refs=("e1",),
                ),
            ),
            ReportBlock(
                block_id="b5",
                role=BlockRole.RECOMMENDATION,
                body="加大对渠道 A 投入",
                evidence_refs=("e1",),
                process_refs=(ProcessRef(step_id="s1", note="聚合"),),
            ),
            ReportBlock(
                block_id="b6",
                role=BlockRole.CAVEAT,
                body="7-06 为部分周期,数据不完整",
            ),
            ReportBlock(
                block_id="b7",
                role=BlockRole.TABLE,
                table_columns=("渠道", "GMV"),
                table_rows=(("A", "72000"), ("B", "48000")),
            ),
        ),
    )


def test_rich_document_roundtrip(rich_document: ReportDocument) -> None:
    rebuilt = ReportDocument.from_dict(rich_document.to_dict())
    assert rebuilt == rich_document


def test_contract_with_none_contract_roundtrip():
    doc = ReportDocument(title="裸文档")
    assert ReportDocument.from_dict(doc.to_dict()) == doc


def test_kpi_cards_nested_roundtrip():
    block = ReportBlock(
        block_id="k",
        role=BlockRole.KPI_STRIP,
        kpi_cards=((("label", "GMV"), ("value", "120")),),
    )
    rebuilt = ReportBlock.from_dict(block.to_dict())
    assert rebuilt.kpi_cards == ((("label", "GMV"), ("value", "120")),)
    # 全不可变
    assert isinstance(rebuilt.kpi_cards[0], tuple)


def test_field_sources_roundtrip(rich_document: ReportDocument) -> None:
    payload = rich_document.to_dict()
    assert payload["contract"]["field_sources"] == [
        ["report_type", "implicit_user"],
        ["audience", "explicit_user"],
    ]
    assert rich_document.contract.field_sources[1][1] is SourceKind.EXPLICIT_USER


def test_enum_serializes_to_value():
    c = ReportContract(question="q", report_type=ReportType.DAILY_KPI)
    payload = c.to_dict()
    assert payload["report_type"] == "daily_kpi"


def test_to_dict_is_json_serializable(rich_document: ReportDocument) -> None:
    json.dumps(rich_document.to_dict())


def test_evidence_ref_roundtrip():
    er = EvidenceRef(
        evidence_id="e1",
        tool_call_id="tu1",
        source_table="sales.csv",
        row_count=100,
        artifact_path="/out/chart.png",
    )
    assert EvidenceRef.from_dict(er.to_dict()) == er


def test_frozen_contract():
    c = ReportContract(question="q")
    with pytest.raises(FrozenInstanceError):
        c.question = "other"  # type: ignore[misc]


def test_hashable_block():
    assert hash(ReportBlock(block_id="b", role=BlockRole.FINDING)) is not None
