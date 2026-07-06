"""Wave 1 reporting.traceability: 契约字段溯源映射 + 中读解释 + 分组。"""

from __future__ import annotations

from data_analysis_agent.reporting.context_collector import build_data_context
from data_analysis_agent.reporting.model import (
    ColumnInfo,
    DataContext,
    ExplicitRequirements,
    ImplicitRequirements,
    ProcessContext,
    SourceKind,
    TableInfo,
    UserNeed,
)
from data_analysis_agent.reporting.requirement_parser import parse_user_need
from data_analysis_agent.reporting.traceability import (
    explain_link,
    index_by_target,
    link_to_contract_fields,
)


def _sales_data_context() -> DataContext:
    return DataContext(
        tables=(
            TableInfo(
                name="sales.csv",
                path="/data/sales.csv",
                columns=(
                    ColumnInfo("order_date", "datetime64", "date"),
                    ColumnInfo("amount", "float64", "metric"),
                    ColumnInfo("region", "object", "dimension"),
                ),
                n_rows=100,
            ),
        ),
        candidate_date_columns=("order_date",),
        candidate_metric_columns=("amount",),
        candidate_dimensions=("region",),
        business_grain="order",
    )


def test_daily_report_links():
    need = parse_user_need("给我看看上周销售日报,要能给领导看")
    dc = _sales_data_context()
    links = link_to_contract_fields(need, dc, ProcessContext())
    by_target = index_by_target(links)

    # report_type ← 隐式推断
    assert "report_type" in by_target
    assert by_target["report_type"][0].source is SourceKind.IMPLICIT_USER

    # audience / language ← 显式
    assert by_target["audience"][0].source is SourceKind.EXPLICIT_USER
    assert by_target["language"][0].source is SourceKind.EXPLICIT_USER

    # time_window ← 数据上下文(用户未明示时间值,但有日期列)
    assert "time_window" in by_target
    assert by_target["time_window"][0].source is SourceKind.DATA_CONTEXT

    # metrics / dimensions / business_grain / data_sources ← 数据上下文
    assert by_target["metrics"][-1].source is SourceKind.DATA_CONTEXT
    assert by_target["dimensions"][0].source is SourceKind.DATA_CONTEXT
    assert by_target["business_grain"][0].source_ref == "order"
    assert by_target["data_sources"][0].source_ref == "/data/sales.csv"

    # comparison 无依据 → 不产 link
    assert "comparison" not in by_target


def test_explicit_named_metric_link():
    need = UserNeed(
        raw_request="看 gmv",
        explicit_requirements=ExplicitRequirements(named_metrics=("gmv",)),
        implicit_requirements=ImplicitRequirements(),
    )
    links = link_to_contract_fields(need, DataContext(), ProcessContext())
    by_target = index_by_target(links)
    assert "metrics" in by_target
    assert any(
        lk.source is SourceKind.EXPLICIT_USER and lk.source_ref == "gmv"
        for lk in by_target["metrics"]
    )


def test_no_links_for_empty_context():
    need = UserNeed(
        raw_request="分析一下",
        explicit_requirements=ExplicitRequirements(),
        implicit_requirements=ImplicitRequirements(),
    )
    links = link_to_contract_fields(need, DataContext(), ProcessContext())
    assert links == ()


def test_explain_link_human_readable():
    need = parse_user_need("上周销售日报")
    dc = _sales_data_context()
    links = link_to_contract_fields(need, dc, ProcessContext())
    for lk in links:
        text = explain_link(lk)
        assert lk.target in text
        assert len(text) > 0
    # comparison 解释不存在(comparison 无 link)
    targets = {lk.target for lk in links}
    assert "comparison" not in targets


def test_index_by_target_groups_multiple_sources():
    dc = build_data_context(
        {
            "kind": "file",
            "path": "/data/s.csv",
            "tables": [
                {
                    "columns": [
                        {"name": "amount", "dtype": "float64"},
                        {"name": "qty", "dtype": "int64"},
                    ]
                }
            ],
        }
    )
    need = UserNeed(
        raw_request="看 amount",
        explicit_requirements=ExplicitRequirements(named_metrics=("amount",)),
        implicit_requirements=ImplicitRequirements(),
    )
    links = link_to_contract_fields(need, dc, ProcessContext())
    by_target = index_by_target(links)
    # metrics 既有 explicit(amount) 又有 data(amount, qty)
    metric_sources = {(lk.source, lk.source_ref) for lk in by_target["metrics"]}
    assert (SourceKind.EXPLICIT_USER, "amount") in metric_sources
    assert (SourceKind.DATA_CONTEXT, "amount") in metric_sources
    assert (SourceKind.DATA_CONTEXT, "qty") in metric_sources
