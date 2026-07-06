"""Wave 1 reporting.model: 构造、默认值、to_dict/from_dict 往返、frozen 不可变。"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from data_analysis_agent.reporting.model import (
    ColumnInfo,
    DataContext,
    ExplicitRequirements,
    ImplicitRequirements,
    ProcessContext,
    ProcessStep,
    SourcedValue,
    SourceKind,
    TableInfo,
    TraceLink,
    Uncertainty,
    UserNeed,
)

# ----------------------------- 构造与默认值 -----------------------------


def test_sourced_value_defaults():
    sv = SourcedValue(value="日报", source=SourceKind.IMPLICIT_USER)
    assert sv.rationale == ""
    assert sv.source is SourceKind.IMPLICIT_USER


def test_explicit_requirements_defaults_empty():
    er = ExplicitRequirements()
    assert er.business_question is None
    assert er.requested_outputs == ()
    assert er.must_avoid == ()


def test_user_need_construction():
    un = UserNeed(
        raw_request="上周销售日报",
        explicit_requirements=ExplicitRequirements(),
        implicit_requirements=ImplicitRequirements(likely_report_type="daily_kpi"),
        uncertainties=(Uncertainty(topic="comparison", why="未明示基线"),),
    )
    assert un.raw_request == "上周销售日报"
    assert un.clarification_needed is False
    assert len(un.uncertainties) == 1


# ----------------------------- 往返契约 -----------------------------


@pytest.fixture
def roundtrip_objects() -> list[object]:
    return [
        UserNeed(
            raw_request="x",
            explicit_requirements=ExplicitRequirements(business_question="q"),
            implicit_requirements=ImplicitRequirements(likely_report_type="diagnostic"),
        ),
        UserNeed(
            raw_request="y",
            explicit_requirements=ExplicitRequirements(named_metrics=("gmv",)),
            implicit_requirements=ImplicitRequirements(),
            uncertainties=(Uncertainty(topic="period", why="absent", needs_clarification=True),),
            clarification_needed=True,
        ),
        DataContext(
            tables=(
                TableInfo(
                    name="s.csv",
                    path="/a/s.csv",
                    columns=(ColumnInfo("date", "datetime", "date"),),
                    n_rows=10,
                    n_rows_sampled=10,
                    sampled=True,
                ),
            ),
            candidate_date_columns=("date",),
            candidate_metric_columns=("amount",),
            available_date_range=("2026-06-01", "2026-06-30"),
        ),
        DataContext(available_date_range=(None, "2026-07-06")),
        ProcessContext(
            steps=(
                ProcessStep(
                    step_id="s1",
                    tool="python_analysis",
                    summary="agg by day",
                    assumptions=("前提:订单号唯一",),
                    evidence_ids=("e1",),
                    artifact_ids=("a1",),
                ),
                ProcessStep(
                    step_id="s2",
                    tool="html_report",
                    summary="render",
                    failed=True,
                    recovery="rerun",
                ),
            ),
            rejected_paths=("hypothesis A",),
        ),
        ProcessContext(sensitive_mode=True),
        TraceLink(target="report_type", source=SourceKind.IMPLICIT_USER, source_ref="日报"),
        SourcedValue(value=None, source=SourceKind.DATA_CONTEXT, rationale="col x"),
    ]


def test_roundtrip(roundtrip_objects: list[object]) -> None:
    for obj in roundtrip_objects:
        rebuilt = type(obj).from_dict(obj.to_dict())  # type: ignore[attr-defined]
        assert rebuilt == obj, f"round-trip mismatch for {type(obj).__name__}"


def test_enum_serializes_to_value():
    tl = TraceLink(target="t", source=SourceKind.EXPLICIT_USER, source_ref="r")
    payload = tl.to_dict()
    assert payload["source"] == "explicit_user"
    assert isinstance(payload["source"], str)


def test_to_dict_is_json_serializable(roundtrip_objects: list[object]) -> None:
    for obj in roundtrip_objects:
        json.dumps(obj.to_dict())  # type: ignore[attr-defined]


def test_available_date_range_with_none_roundtrip():
    dc = DataContext(available_date_range=(None, "2026-07-06"))
    assert DataContext.from_dict(dc.to_dict()).available_date_range == (None, "2026-07-06")


def test_from_dict_ignores_unknown_keys():
    un = UserNeed(
        raw_request="x",
        explicit_requirements=ExplicitRequirements(),
        implicit_requirements=ImplicitRequirements(),
    )
    payload = un.to_dict()
    payload["__unknown__"] = "ignored"
    assert UserNeed.from_dict(payload) == un


# ----------------------------- frozen 不可变 -----------------------------


def test_frozen_user_need():
    un = UserNeed(
        raw_request="x",
        explicit_requirements=ExplicitRequirements(),
        implicit_requirements=ImplicitRequirements(),
    )
    with pytest.raises(FrozenInstanceError):
        un.raw_request = "y"  # type: ignore[misc]
    assert replace(un, raw_request="z").raw_request == "z"


def test_frozen_trace_link():
    tl = TraceLink(target="t", source=SourceKind.TEMPLATE, source_ref="r")
    with pytest.raises(FrozenInstanceError):
        tl.target = "u"  # type: ignore[misc]


def test_hashable():
    assert hash(TraceLink(target="t", source=SourceKind.TEMPLATE, source_ref="r")) is not None
    assert (
        hash(
            UserNeed(
                raw_request="x",
                explicit_requirements=ExplicitRequirements(),
                implicit_requirements=ImplicitRequirements(),
            )
        )
        is not None
    )
