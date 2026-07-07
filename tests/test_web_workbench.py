"""Wave 8 web workbench: TestClient 各端点 + artifact 路径防护 + draft/ready QA。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from data_analysis_agent.web.app import create_app  # noqa: E402

_PROFILE = {
    "kind": "file",
    "path": "/data/sales.csv",
    "format": "csv",
    "tables": [
        {
            "columns": [
                {"name": "order_date", "dtype": "datetime64"},
                {"name": "amount", "dtype": "float64"},
            ],
            "n_rows_sampled": 100,
        }
    ],
}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(artifact_dir=tmp_path))


def test_serves_ui(tmp_path: Path):
    c = _client(tmp_path)
    res = c.get("/")
    assert res.status_code == 200
    assert "Workbench" in res.text


def test_report_need(tmp_path: Path):
    c = _client(tmp_path)
    res = c.post("/api/report/need", json={"raw_request": "上周销售日报,给领导看"})
    assert res.status_code == 200
    data = res.json()
    assert data["implicit_requirements"]["likely_report_type"] == "daily_kpi"
    assert data["explicit_requirements"]["audience"] == "business_stakeholder"


def test_report_context(tmp_path: Path):
    c = _client(tmp_path)
    res = c.post("/api/report/context", json={"profile": _PROFILE})
    assert res.status_code == 200
    dc = res.json()["data_context"]
    assert "order_date" in dc["candidate_date_columns"]
    assert "amount" in dc["candidate_metric_columns"]


def test_report_contract(tmp_path: Path):
    c = _client(tmp_path)
    res = c.post("/api/report/contract", json={"question": "上周销售日报"})
    assert res.status_code == 200
    contract = res.json()
    assert len(contract["field_sources"]) > 0
    assert contract["report_type"] == "daily_kpi"


def test_qa_draft(tmp_path: Path):
    """缺 data_scope + 缺 artifact → readiness=draft。"""
    c = _client(tmp_path)
    res = c.post(
        "/api/qa",
        json={
            "document": {
                "title": "x",
                "contract": {"question": "q", "explicit_requirement_refs": ["u1"]},
            },
            "artifact_exists": False,
        },
    )
    assert res.status_code == 200
    assert res.json()["readiness"] == "draft"


def test_qa_ready(tmp_path: Path):
    """干净 doc + artifact_exists=True → readiness=ready。"""
    c = _client(tmp_path)
    res = c.post(
        "/api/qa",
        json={
            "document": {
                "title": "x",
                "data_scope": "sales.csv",
                "contract": {"question": "q", "explicit_requirement_refs": ["u1"]},
                "blocks": [
                    {"block_id": "s", "role": "executive_summary", "body": "结论"},
                    {
                        "block_id": "r",
                        "role": "recommendation",
                        "body": "建议 A",
                        "evidence_refs": ["e1"],
                    },
                    {"block_id": "src", "role": "source_metadata", "body": "s"},
                ],
            },
            "artifact_exists": True,
        },
    )
    assert res.status_code == 200
    assert res.json()["readiness"] == "ready"


def test_template_match(tmp_path: Path):
    c = _client(tmp_path)
    res = c.get("/api/template", params={"text": "上周销售日报"})
    assert res.status_code == 200
    assert "section_roles" in res.json()


def test_template_404_on_ambiguous(tmp_path: Path):
    c = _client(tmp_path)
    res = c.get("/api/template", params={"text": "分析一下"})
    assert res.status_code == 404


# ----------------------------- artifact 安全预览 -----------------------------


def test_artifact_serves_html(tmp_path: Path):
    (tmp_path / "report.html").write_text("<h1>ok</h1>", encoding="utf-8")
    c = _client(tmp_path)
    res = c.get("/artifacts/report.html")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_artifact_rejects_nonexistent(tmp_path: Path):
    c = _client(tmp_path)
    assert c.get("/artifacts/nonexistent.html").status_code == 404


def test_artifact_rejects_non_html(tmp_path: Path):
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    c = _client(tmp_path)
    assert c.get("/artifacts/data.json").status_code == 404


def test_artifact_rejects_escape_attempts(tmp_path: Path):
    """路径遍历/点开头/Windows 保留/编码变体 → 全 404。"""
    c = _client(tmp_path)
    bad_names = [
        "../evil.html",  # 路径遍历(routing 不匹配 / → 404)
        ".hidden.html",  # 点开头
        "CON.html",  # Windows 保留
        "x.",  # 点结尾
    ]
    for name in bad_names:
        res = c.get(f"/artifacts/{name}")
        assert res.status_code == 404, f"{name!r} 应被拒"


def test_artifact_rejects_encoded_traversal(tmp_path: Path):
    """URL 编码遍历变体(评审 Medium)。"""
    c = _client(tmp_path)
    for encoded in ["%2e%2eevil.html", "%2e%2f%2eevil.html"]:
        res = c.get(f"/artifacts/{encoded}")
        assert res.status_code in (404, 422), f"{encoded!r} 应被拒"


# ----------------------------- 评审 Critical/High/Medium 修复验证 -----------------------------


def test_contract_qa_closed_loop(tmp_path: Path):
    """contract 端点产的 contract 喂 QA 不触发 contract.no_traceability(评审 Critical 修复)。

    修复前:contract 端点漏填四类 ref → QA 必判断链 → readiness 永远 draft。
    修复后:ref 桶式映射 + field_sources + data_gaps → 闭环通过。
    """
    c = _client(tmp_path)
    contract = c.post("/api/report/contract", json={"question": "上周销售日报"}).json()
    refs = (
        contract["explicit_requirement_refs"],
        contract["implicit_requirement_refs"],
        contract["data_context_refs"],
        contract["process_context_refs"],
    )
    assert any(refs), "至少一类 ref 非空(否则 QA 判断链)"
    qa = c.post(
        "/api/qa",
        json={
            "document": {"title": "x", "contract": contract, "data_scope": "s"},
            "artifact_exists": True,
        },
    ).json()
    assert not any(f["code"] == "contract.no_traceability" for f in qa["findings"])


def test_contract_malformed_user_need(tmp_path: Path):
    """残缺 user_need dict → 回退 parse_user_need(question),不 500(评审 Medium)。"""
    c = _client(tmp_path)
    res = c.post(
        "/api/report/contract",
        json={"question": "上周销售日报", "user_need": {"raw_request": "x"}},
    )
    assert res.status_code == 200
    assert res.json()["report_type"] == "daily_kpi"


# ----------------------------- 反馈捕获(§8 acceptance #3) -----------------------------


def test_feedback_stores(tmp_path: Path):
    """反馈标签追加 JSONL(spec §5.4 feedback tags;§8 Wave 8 acceptance #3)。"""
    c = _client(tmp_path)
    res = c.post(
        "/api/feedback",
        json={
            "tags": ["wrong_metric", "weak_chart"],
            "comment": "GMV 口径未确认",
            "readiness": "needs_review",
        },
    )
    assert res.status_code == 200
    assert res.json()["stored"] is True
    feedback_file = tmp_path / "feedback.jsonl"
    assert feedback_file.exists()
    records = [json.loads(line) for line in feedback_file.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert "wrong_metric" in records[0]["tags"]
    assert records[0]["readiness"] == "needs_review"
