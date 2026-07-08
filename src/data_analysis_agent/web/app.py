"""FastAPI workbench app(Wave 8 MVP):交互式报告塑造,消费 reporting 纯函数。

端点全确定性(无 LLM):need/context/contract/qa/template + artifact 安全预览 + UI。
artifact 路径防护镜像 html_report(NUL/目录/点开头/点空格/Windows 保留 + .html only)。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from data_analysis_agent.reporting.context_collector import (
    build_data_context,
    build_process_context,
)
from data_analysis_agent.reporting.contract import (
    Audience,
    ReportContract,
    ReportDocument,
    ReportType,
)
from data_analysis_agent.reporting.model import DataContext, ProcessContext, SourceKind, UserNeed
from data_analysis_agent.reporting.qa import run_qa
from data_analysis_agent.reporting.requirement_parser import parse_user_need
from data_analysis_agent.reporting.templates import match_template
from data_analysis_agent.reporting.traceability import link_to_contract_fields

from .schemas import (
    ContextRequest,
    ContractRequest,
    FeedbackRequest,
    NeedRequest,
    QARequest,
    QAResponse,
)

_STATIC_DIR = Path(__file__).parent / "static"
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# 四类 ref 桶式映射(镜像 report_contract 工具,评审 Critical:不填则 QA 必判断链)
_REF_BUCKET: dict[SourceKind, str] = {
    SourceKind.EXPLICIT_USER: "explicit_requirement_refs",
    SourceKind.IMPLICIT_USER: "implicit_requirement_refs",
    SourceKind.DATA_CONTEXT: "data_context_refs",
    SourceKind.PROCESS_CONTEXT: "process_context_refs",
}


def _resolve_report_type(override: str | None, user_need: UserNeed | None) -> ReportType:
    raw = override or (user_need.implicit_requirements.likely_report_type if user_need else None)
    if not raw:
        return ReportType.AD_HOC
    try:
        return ReportType(raw)
    except ValueError:
        return ReportType.AD_HOC


def _resolve_audience(override: str | None, user_need: UserNeed | None) -> Audience:
    raw = override or (user_need.explicit_requirements.audience if user_need else None)
    if not raw:
        return Audience.BUSINESS_STAKEHOLDER
    try:
        return Audience(raw)
    except ValueError:
        return Audience.BUSINESS_STAKEHOLDER


def _validate_artifact_name(name: str) -> str | None:
    """镜像 html_report 的 bare-name 规则 + .html only。返 None=OK / str=错误。"""
    if "\x00" in name:
        return "NUL"
    if Path(name).name != name or name.startswith("."):
        return "path"
    if not name.endswith(".html"):
        return "non-html"
    if name.endswith((".", " ")):
        return "suffix"
    stem = name.split(".", 1)[0].strip().upper()
    if stem in _WINDOWS_RESERVED:
        return "reserved"
    return None


def create_app(artifact_dir: str | Path | None = None) -> FastAPI:
    """创建 FastAPI workbench app。artifact_dir 为 HTML 报告产物目录。"""
    if artifact_dir is None:
        artifact_dir = Path(tempfile.mkdtemp(prefix="daa_web_"))
    artifacts = Path(artifact_dir).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="DataAnalysisAgent Report Workbench", version="0.1.0")
    app.state.artifact_dir = artifacts

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.post("/api/report/need")
    def report_need(req: NeedRequest) -> dict[str, Any]:
        return parse_user_need(req.raw_request).to_dict()

    @app.post("/api/report/context")
    def report_context(req: ContextRequest) -> dict[str, Any]:
        dc = build_data_context(req.profile)
        pc = build_process_context(req.events or [], sensitive_mode=req.sensitive_mode)
        return {"data_context": dc.to_dict(), "process_context": pc.to_dict()}

    @app.post("/api/report/contract")
    def report_contract(req: ContractRequest) -> dict[str, Any]:
        question = req.question
        if req.user_need:
            try:
                un = UserNeed.from_dict(req.user_need)
            except (TypeError, KeyError):
                un = parse_user_need(question)  # 残缺 dict 回退(评审 Medium)
        else:
            un = parse_user_need(question)
        dc = DataContext.from_dict(req.data_context) if req.data_context else DataContext()
        pc = (
            ProcessContext.from_dict(req.process_context)
            if req.process_context
            else ProcessContext()
        )
        links = link_to_contract_fields(un, dc, pc)
        # 四类 ref 桶式映射(评审 Critical:不填则 QA 必触发 contract.no_traceability)
        ref_buckets: dict[str, list[str]] = {
            "explicit_requirement_refs": [],
            "implicit_requirement_refs": [],
            "data_context_refs": [],
            "process_context_refs": [],
        }
        for lk in links:
            bucket = _REF_BUCKET.get(lk.source)
            if bucket and lk.source_ref:
                ref_buckets[bucket].append(lk.source_ref)
        # missing_context = uncertainties + data_gaps(评审 Medium)
        missing = [u.topic for u in un.uncertainties]
        for gap in dc.data_gaps:
            if gap not in missing:
                missing.append(gap)
        contract = ReportContract(
            question=question,
            report_type=_resolve_report_type(req.report_type, un),
            audience=_resolve_audience(req.audience, un),
            language=req.language or un.explicit_requirements.language or "auto",
            data_sources=tuple(tb.path or tb.name for tb in dc.tables),
            dimensions=tuple(dc.candidate_dimensions),
            business_grain=dc.business_grain,
            explicit_requirement_refs=tuple(ref_buckets["explicit_requirement_refs"]),
            implicit_requirement_refs=tuple(ref_buckets["implicit_requirement_refs"]),
            data_context_refs=tuple(ref_buckets["data_context_refs"]),
            process_context_refs=tuple(ref_buckets["process_context_refs"]),
            field_sources=tuple((lk.target, lk.source) for lk in links),
            missing_context=tuple(missing),
        )
        return contract.to_dict()

    @app.post("/api/qa", response_model=QAResponse)
    def qa(req: QARequest) -> dict[str, Any]:
        doc = ReportDocument.from_dict(req.document)
        report = run_qa(
            doc,
            artifact_exists=req.artifact_exists,
            n_points_by_chart=req.n_points_by_chart,
            n_observations_by_chart=req.n_observations_by_chart,
        )
        return {
            "readiness": report.readiness.value,
            "artifact_exists": report.artifact_exists,
            "findings": [
                {
                    "severity": f.severity.value,
                    "code": f.code,
                    "message": f.message,
                    "block_id": f.block_id,
                    "suggested_fix": f.suggested_fix,
                }
                for f in report.findings
            ],
        }

    @app.get("/api/template")
    def template(text: str) -> dict[str, Any]:
        tpl = match_template(text)
        if tpl is None:
            raise HTTPException(status_code=404, detail="no template matched")
        return tpl.to_dict()

    @app.get("/artifacts/{name}")
    def artifact(name: str) -> FileResponse:
        err = _validate_artifact_name(name)
        if err:
            raise HTTPException(status_code=404, detail=f"invalid artifact name: {err}")
        path = (artifacts / name).resolve()
        if not path.is_relative_to(artifacts) or not path.exists():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(path, media_type="text/html", headers={"Content-Disposition": "inline"})

    @app.post("/api/feedback")
    def feedback(req: FeedbackRequest) -> dict[str, Any]:
        """捕获报告反馈标签(spec §5.4 feedback tags;§8 Wave 8 acceptance #3)。追加 JSONL。"""
        record = {"tags": req.tags, "comment": req.comment, "readiness": req.readiness}
        feedback_path = artifacts / "feedback.jsonl"
        with feedback_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"stored": True, "path": str(feedback_path)}

    # ---- 非确定性端点(需 LLM 运行时;当前 stub,后续统一测试) ----

    @app.get("/api/run/stream")
    def run_stream_stub() -> dict[str, Any]:
        """Live agent 事件流 stub(spec §11 MVP 'event stream')。

        实现需:(1) 放宽 drift(web→runtime)或新建 server/ 包;(2) WebSocket/SSE;
        (3) Anthropic API key。当前返回架构需求说明。
        """
        return {
            "status": "not_implemented",
            "reason": "requires drift rule relaxation (web→runtime) + LLM client + WebSocket",
            "spec_ref": "§11 MVP event stream; §8 Wave 8 live agent event stream",
        }

    @app.post("/api/report/rerun")
    def rerun_stub() -> dict[str, Any]:
        """Correction+rerun stub(spec §8 Wave 8 acceptance #2 'correct intent before rerun')。

        实现需:(1) agent 回路(运行 agent 产新报告);(2) 接收修正后的 contract/need;
        (3) 重新跑分析 + 渲染。当前返回架构需求说明。
        """
        return {
            "status": "not_implemented",
            "reason": "requires agent loop integration (run agent with modified contract)",
            "spec_ref": "§8 Wave 8 acceptance #2 correction+rerun",
        }

    return app
