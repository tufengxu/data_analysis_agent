"""FastAPI app: localhost workbench that runs the agent and streams events over SSE.

Slice 1 (Wave 2 / P1-3): the live-agent run + event codec. Upload / approval /
feedback UI are later slices. The run goes through ``AgentRuntime.from_config``
so the Web runs the SAME agent as the CLI (same tools, skills, permission engine).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ..config import AgentConfig
from ..runtime import AgentRuntime
from ..web.app import create_app as create_web_app
from .approval import WebApprovalHandler, approval_ui
from .event_codec import encode

_STATIC_DIR = Path(__file__).parent / "static"

# 允许上传的数据格式(二进制,故用裸请求体流式而非 multipart——免 python-multipart 依赖)。
_UPLOAD_EXTS = frozenset({".csv", ".xlsx", ".xls", ".parquet"})
_UPLOAD_MAX_BYTES = 200 * 1024 * 1024  # 200MB 上限,防滥用(localhost-only)


class RunRequest(BaseModel):
    """One analysis request from the browser."""

    query: str
    paths: list[str] = []  # authorized data files/dirs (absolute)
    project: str | None = None  # optional project id to run inside


class ApprovalVerdict(BaseModel):
    """The browser's allow/deny decision for a pending AWAITING_CONFIRMATION."""

    approved: bool


def _safe_upload_name(name: str) -> str | None:
    """bare 文件名(无路径、非点开头);非法返 None。镜像 web 的 artifact 名防护。"""
    if not name or "\x00" in name:
        return None
    if Path(name).name != name or name.startswith("."):
        return None
    return name


def create_app(
    config: AgentConfig | None = None,
    *,
    client: Any = None,
    artifact_dir: str | Path | None = None,
) -> FastAPI:
    """Build the unified workbench app. ``client`` lets tests inject a fake LLM client.

    ``artifact_dir`` is where generated HTML reports + feedback.jsonl live; it is
    forwarded to the report workbench sub-app. The web report routes are mounted
    under /workbench so one app (single 127.0.0.1 port) serves BOTH the live run
    and the report/QA/artifact/feedback panels — the product's single workbench.
    """
    config = config or AgentConfig.from_env()
    app = FastAPI(title="DataAnalysisAgent Workbench", version="0.1.0")
    app.state.config = config
    app.state.client = client
    # Web approval handler bound per run inside _stream (single-run workbench);
    # /api/approval resolves the pending AWAITING_CONFIRMATION decision.
    app.state.approval_handler = WebApprovalHandler()

    # Mount the report workbench (web/) under /workbench; routes stay relative.
    app.mount("/workbench", create_web_app(artifact_dir))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.post("/api/run/stream")
    async def run_stream(req: RunRequest) -> StreamingResponse:
        if not config.api_key:
            raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY not set")
        return StreamingResponse(
            _stream(req, config, client, app.state.approval_handler),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/approval")
    def approval(verdict: ApprovalVerdict) -> dict[str, Any]:
        """浏览器的审批决定(#27);无 pending 决定则 fail-closed 返回 not pending。"""
        ok = app.state.approval_handler.resolve(verdict.approved)
        if not ok:
            return {"resolved": False, "reason": "no pending approval"}
        return {"resolved": True, "approved": verdict.approved}

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        """可选 project 列表(前端 project 选择器,#31)。"""
        from ..workspace import Project

        return {
            "projects": [
                {"project_id": p.project_id, "uploads_dir": str(p.uploads_dir)}
                for p in Project.list_projects()
            ]
        }

    @app.post("/api/upload")
    async def upload(request: Request, project: str, filename: str) -> dict[str, Any]:
        """流式上传一个数据文件到 project 的 uploads/(#24,后端缺口)。

        裸请求体(二进制)而非 multipart:CSV/XLSX/Parquet 都是二进制,流式写盘
        免 python-multipart 依赖且对大文件友好。路径防护 + 扩展名白名单 +
        大小上限,fail-closed。``?project=..&filename=..`` 为 query 参数。
        """
        from ..workspace import Project

        safe = _safe_upload_name(filename)
        if safe is None:
            raise HTTPException(status_code=400, detail="invalid filename")
        ext = Path(safe).suffix.lower()
        if ext not in _UPLOAD_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported type {ext!r}; allowed: {sorted(_UPLOAD_EXTS)}",
            )
        try:
            proj = Project.open(project)
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(status_code=404, detail=f"project not readable: {project}") from exc
        uploads = proj.uploads_dir
        uploads.mkdir(parents=True, exist_ok=True)
        dest = (uploads / safe).resolve()
        if not dest.is_relative_to(uploads.resolve()):
            raise HTTPException(status_code=400, detail="invalid filename")
        written = 0
        with open(dest, "wb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                if written > _UPLOAD_MAX_BYTES:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="file too large")
                fh.write(chunk)
        return {"path": str(dest), "size": written, "filename": safe}

    return app


async def _stream(
    req: RunRequest,
    config: AgentConfig,
    client: Any,
    approval_handler: WebApprovalHandler,
) -> Any:
    """Run one agent turn and yield SSE ``data: <json>\\n\\n`` frames."""
    # Fail closed: drop blank/whitespace entries, then require ≥1 real path.
    # With none, the agent would otherwise default to the server process's cwd
    # (the CLI-era convenience) — a footgun for a Web launch. `Path("")` resolves
    # to cwd, so blank entries MUST be filtered, not just the empty-list case.
    paths = [p.strip() for p in req.paths if p and p.strip()]
    if not paths:
        yield _frame(
            {
                "type": "error",
                "error": "no authorized data paths; pass absolute `paths` (upload UI is a later slice).",
            }
        )
        return
    project = None
    if req.project:
        # Imported lazily so a Web run without a project never pays for it.
        from ..workspace import Project

        try:
            project = Project.open(req.project)
        except (KeyError, ValueError, OSError):
            yield _frame({"type": "error", "error": f"project not readable: {req.project}"})
            return
    runtime = None
    try:
        runtime = AgentRuntime.from_config(
            config,
            client=client,
            analysis_paths=paths,
            project=project,
            approval_handler=approval_handler,
        )
        async for event in approval_ui(approval_handler)(runtime.session.send(req.query)):
            yield _frame(encode(event))
    except Exception as exc:  # never crash the SSE mid-stream; surface as an error frame
        yield _frame({"type": "error", "error": str(exc)})
    finally:
        # Shield teardown from client-disconnect cancellation so the kernel is
        # always reaped even if the browser goes away mid-stream.
        if runtime is not None:
            with anyio.CancelScope(shield=True):
                await runtime.shutdown()


def _frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
