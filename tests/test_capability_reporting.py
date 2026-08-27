"""reporting 能力域:注册、成功/失败信封、产物落盘与目录限定(无 LLM、无网络)。"""

from __future__ import annotations

import json
from pathlib import Path

from data_analysis_agent.capabilities import CapabilityRegistry, OutputKind, Permission
from data_analysis_agent.capabilities.reporting import register_all

_NAMES = [
    "reporting_report_need",
    "reporting_report_context",
    "reporting_report_contract",
    "reporting_render_chart",
    "reporting_render_html",
]

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


def _registry(tmp_path: Path) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    names = register_all(registry, artifact_root=tmp_path)
    assert names == _NAMES
    return registry


# ----------------------------- 注册与契约声明 -----------------------------


def test_registers_all_names_in_order(tmp_path: Path):
    registry = CapabilityRegistry()
    assert register_all(registry, artifact_root=tmp_path) == _NAMES
    for name in _NAMES:
        assert registry.has(name)


def test_specs_declare_permission_and_output_kind(tmp_path: Path):
    registry = _registry(tmp_path)
    for name in _NAMES[:3]:
        spec = registry.get(name)
        assert spec.domain == "reporting"
        assert spec.permission is Permission.READ_ONLY
        assert spec.output_kind is OutputKind.STRUCTURED
    for name in _NAMES[3:]:
        spec = registry.get(name)
        assert spec.permission is Permission.WRITES_ARTIFACTS
        assert spec.output_kind is OutputKind.ARTIFACT
        assert spec.error_codes == ("validation_error", "execution_error")


# ----------------------------- reporting_report_need -----------------------------


async def test_report_need_parses(tmp_path: Path):
    env = await _registry(tmp_path).execute(
        "reporting_report_need", {"raw_request": "上周销售日报,给领导看"}
    )
    assert env["ok"] is True
    un = env["data"]["user_need"]
    assert un["implicit_requirements"]["likely_report_type"] == "daily_kpi"
    assert un["explicit_requirements"]["audience"] == "business_stakeholder"
    assert "daily_kpi" in env["content"]


async def test_report_need_validation_error(tmp_path: Path):
    for bad in ({}, {"raw_request": "  "}):
        env = await _registry(tmp_path).execute("reporting_report_need", bad)
        assert env["ok"] is False
        assert env["error"]["code"] == "validation_error"


# ----------------------------- reporting_report_context -----------------------------


async def test_report_context_builds(tmp_path: Path):
    env = await _registry(tmp_path).execute("reporting_report_context", {"profile": _PROFILE})
    assert env["ok"] is True
    dc = env["data"]["data_context"]
    assert "order_date" in dc["candidate_date_columns"]
    assert "amount" in dc["candidate_metric_columns"]
    assert env["data"]["process_context"]["steps"] == []


async def test_report_context_sensitive_mode_drops_steps(tmp_path: Path):
    env = await _registry(tmp_path).execute(
        "reporting_report_context",
        {
            "profile": _PROFILE,
            "events": [{"step_id": "s1", "tool": "python_analysis", "summary": "agg"}],
            "sensitive_mode": True,
        },
    )
    pc = env["data"]["process_context"]
    assert pc["sensitive_mode"] is True
    assert pc["steps"] == []


async def test_report_context_validation_error(tmp_path: Path):
    for bad in ({}, {"profile": "not-a-dict"}):
        env = await _registry(tmp_path).execute("reporting_report_context", bad)
        assert env["error"]["code"] == "validation_error"


# ----------------------------- reporting_report_contract -----------------------------


async def test_report_contract_traceability_chain(tmp_path: Path):
    registry = _registry(tmp_path)
    need_env = await registry.execute("reporting_report_need", {"raw_request": "上周销售日报"})
    ctx_env = await registry.execute("reporting_report_context", {"profile": _PROFILE})
    env = await registry.execute(
        "reporting_report_contract",
        {
            "question": "上周销售日报",
            "user_need": need_env["data"]["user_need"],
            "data_context": ctx_env["data"]["data_context"],
        },
    )
    assert env["ok"] is True
    contract = env["data"]["contract"]
    assert contract["report_type"] == "daily_kpi"
    assert len(contract["field_sources"]) > 0
    refs = (
        contract["explicit_requirement_refs"],
        contract["implicit_requirement_refs"],
        contract["data_context_refs"],
        contract["process_context_refs"],
    )
    assert any(refs), "至少一类 ref 非空(否则 QA 会断链)"
    assert env["data"]["template"]["name"]  # daily_kpi 有确定性模板


async def test_report_contract_validation_error(tmp_path: Path):
    for bad in ({}, {"question": "  "}):
        env = await _registry(tmp_path).execute("reporting_report_contract", bad)
        assert env["error"]["code"] == "validation_error"


# ----------------------------- reporting_render_chart -----------------------------


async def test_render_chart_writes_artifact(tmp_path: Path):
    env = await _registry(tmp_path).execute(
        "reporting_render_chart",
        {
            "block_id": "c1",
            "family": "line",
            "data": {
                "labels": ["a", "b", "c"],
                "series": [{"name": "GMV", "values": [1, 2, 3]}],
            },
        },
    )
    assert env["ok"] is True
    assert len(env["artifacts"]) == 1
    path = Path(env["artifacts"][0])
    assert path.is_relative_to(tmp_path)  # 产物限定在 artifact root 之内
    assert path.exists() and path.suffix == ".json"
    option = json.loads(path.read_text(encoding="utf-8"))
    assert option["series"][0]["type"] == "line"
    # 信封 data 同步携带 option + 充分性元数据
    assert env["data"]["chart_option"]["xAxis"]["data"] == ["a", "b", "c"]
    assert env["data"]["chart_meta"]["family"] == "line"
    assert env["data"]["chart_meta"]["block_id"] == "c1"


async def test_render_chart_validation_error(tmp_path: Path):
    env = await _registry(tmp_path).execute(
        "reporting_render_chart",
        {"block_id": "c1", "family": "bogus", "data": {"labels": ["a"]}},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "validation_error"
    env = await _registry(tmp_path).execute(
        "reporting_render_chart",
        {
            "block_id": "../evil",
            "family": "line",
            "data": {"labels": ["a"], "series": [{"values": [1]}]},
        },
    )
    assert env["error"]["code"] == "validation_error"


async def test_render_chart_execution_error_on_non_finite(tmp_path: Path):
    env = await _registry(tmp_path).execute(
        "reporting_render_chart",
        {
            "block_id": "c1",
            "family": "line",
            "data": {"labels": ["a"], "series": [{"values": [float("nan")]}]},
        },
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "execution_error"
    assert "non-finite" in env["error"]["message"]


# ----------------------------- reporting_render_html -----------------------------


async def test_render_html_legacy_writes_confined_artifact(tmp_path: Path):
    env = await _registry(tmp_path).execute(
        "reporting_render_html",
        {
            "title": "销售周报",
            "subtitle": "2026 W27",
            "summary": "GMV 环比上升。",
            "sections": [
                {"heading": "总览", "text": "总 GMV 12 万"},
                {
                    "heading": "明细",
                    "table": {"columns": ["渠道", "GMV"], "rows": [["A", "7"], ["B", "5"]]},
                },
            ],
        },
    )
    assert env["ok"] is True
    assert len(env["artifacts"]) == 1
    path = Path(env["artifacts"][0])
    assert path.is_relative_to(tmp_path)  # 产物根限定(fail-closed)
    assert path.exists() and path.suffix == ".html"
    page = path.read_text(encoding="utf-8")
    assert "销售周报" in page
    assert "<td>A</td>" in page


async def test_render_html_artifact_dir_override(tmp_path: Path):
    other = tmp_path / "other_reports"
    env = await _registry(tmp_path).execute(
        "reporting_render_html",
        {
            "title": "覆盖目录",
            "artifact_dir": str(other),
            "file_name": "custom.html",
            "sections": [{"heading": "总览", "text": "正文"}],
        },
    )
    assert env["ok"] is True
    path = Path(env["artifacts"][0])
    assert path == other / "custom.html"
    assert path.exists()


async def test_render_html_v2_refuses_draft_document(tmp_path: Path):
    """DRAFT(blocker)文档被 QA 闸拒绝:不落盘,fail-closed execution_error 信封。"""

    env = await _registry(tmp_path).execute(
        "reporting_render_html",
        {"document": {"title": "草稿", "blocks": []}},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "execution_error"
    assert "DRAFT" in env["error"]["message"]
    # 未写任何文件
    assert not list((tmp_path / "reports").glob("*.html"))


async def test_render_html_validation_error(tmp_path: Path):
    env = await _registry(tmp_path).execute("reporting_render_html", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "validation_error"
    assert "title" in env["error"]["message"]


async def test_unknown_capability_is_not_found(tmp_path: Path):
    env = await _registry(tmp_path).execute("reporting_nope", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "not_found"
