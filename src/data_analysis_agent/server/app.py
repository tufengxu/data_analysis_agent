"""FastAPI app: localhost workbench that runs the agent and streams events over SSE.

Slice 1 (Wave 2 / P1-3): the live-agent run + event codec. Upload / approval /
feedback UI are later slices. The run goes through ``AgentRuntime.from_config``
so the Web runs the SAME agent as the CLI (same tools, skills, permission engine).
"""

from __future__ import annotations

import contextlib
import json
import secrets
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
    run_id: str | None = None  # which run's pending approval to resolve (per-run handler)


def _safe_upload_name(name: str) -> str | None:
    """bare 文件名(无路径、非点开头);非法返 None。镜像 web 的 artifact 名防护。"""
    if not name or "\x00" in name:
        return None
    if Path(name).name != name or name.startswith("."):
        return None
    return name


def _default_workbench_config() -> AgentConfig:
    """Default config for the real (non-test) workbench: ``local_safe`` preset.

    The Web workbench must be deny-by-default out of the box (roadmap §8:
    "Local-safe mode is the default for Web Workbench"). ``AgentConfig.from_env``
    leaves ``permission_preset=""``, which builds NO permission engine (everything
    allowed) — so a default-started workbench would run mutators with no approval.
    Pinning ``local_safe`` here makes read-only tools ALLOW, known mutators ASK
    (surfaced to the browser approval UI; timeout = deny), unknown tools DENY.
    A caller-supplied ``config`` is respected unchanged (tests inject their own).
    """
    from dataclasses import replace

    return replace(AgentConfig.from_env(), permission_preset="local_safe")


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
    config = config or _default_workbench_config()
    app = FastAPI(title="DataAnalysisAgent Workbench", version="0.1.0")
    app.state.config = config
    app.state.client = client
    # Per-run approval handlers keyed by run_id (review MAJOR: a single shared
    # handler let two concurrent runs cross-apply each other's verdict). Each
    # /api/run/stream gets a fresh handler + run_id; the run_start frame carries
    # the run_id so the browser routes its verdict back to the right run.
    app.state.approval_handlers = {}
    # CSRF guard for same-origin agent-driven endpoints: the served UI embeds this
    # token and must echo it back as X-DAA-Token on /api/run/stream + /api/approval
    # + /api/upload. Blocks a same-origin artifact page from silently driving the
    # agent/approval, and blocks cross-origin form-POST upload (custom header).
    app.state.csrf_token = secrets.token_urlsafe(24)

    # The artifact dir holds BOTH the reporting pipeline's reports AND the agent
    # run's HTML output — in this product they are the same files (the agent's
    # html_report tool output IS the report the user previews). One dir is shared
    # by the runtime (writes) and the workbench (serves). Serving that untrusted
    # HTML inline would be stored-XSS that could drive /api/approval & /api/run/stream,
    # so it is mitigated NOT by directory separation (that breaks preview) but by:
    #   * CSP `sandbox` on the artifact route → opaque origin (web/app.py): the page's
    #     scripts still run (ECharts renders) but it can NOT reach the workbench origin,
    #     so it can neither read the CSRF token nor call the guarded endpoints (HIGH #1);
    #   * the X-DAA-Token CSRF check below → defense in depth for the guarded endpoints;
    #   * the bare-name + .html-only + is_relative_to guard → only .html is served;
    #   * chmod 0o700 → the dir is not world-readable on disk (MEDIUM #2).
    if artifact_dir is None:
        from ..workspace import default_home

        artifact_dir = default_home() / "artifacts"
    artifacts = Path(artifact_dir).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    # Best-effort hardening (review MINOR): the dir is server-owned on a localhost
    # single-user install, so chmod is defense-in-depth, not functional. Don't let a
    # read-only/managed mount (or a locked ~/.daa) abort app construction.
    with contextlib.suppress(OSError):
        artifacts.chmod(0o700)  # not world-readable
    app.state.artifact_dir = artifacts

    # Mount the report workbench (web/) under /workbench; serves `artifacts`.
    # Pass the server's CSRF token so the report UI shares it with the live-run UI
    # (web's /api/feedback checks the same X-DAA-Token).
    app.mount("/workbench", create_web_app(artifacts, csrf_token=app.state.csrf_token))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (
            (_STATIC_DIR / "index.html")
            .read_text(encoding="utf-8")
            .replace("__DAA_CSRF__", app.state.csrf_token)
        )

    @app.post("/api/run/stream")
    async def run_stream(req: RunRequest, request: Request) -> StreamingResponse:
        if not config.api_key:
            raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY not set")
        if request.headers.get("X-DAA-Token") != app.state.csrf_token:
            raise HTTPException(status_code=403, detail="missing/invalid CSRF token")
        # Fresh handler per run, keyed by run_id, so concurrent runs can't
        # cross-apply each other's verdict (review MAJOR).
        run_id = secrets.token_urlsafe(8)
        app.state.approval_handlers[run_id] = WebApprovalHandler()
        return StreamingResponse(
            _http_stream(
                req, config, client, run_id, app.state.approval_handlers, app.state.artifact_dir
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/approval")
    def approval(verdict: ApprovalVerdict, request: Request) -> dict[str, Any]:
        """浏览器的审批决定(#27);无对应 run 的 pending 决定则 fail-closed。"""
        if request.headers.get("X-DAA-Token") != app.state.csrf_token:
            raise HTTPException(status_code=403, detail="missing/invalid CSRF token")
        handler = app.state.approval_handlers.get(verdict.run_id) if verdict.run_id else None
        if handler is None:
            return {"resolved": False, "reason": "unknown run"}
        ok = handler.resolve(verdict.approved)
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
        X-DAA-Token 校验阻止跨源 form-POST 种植数据(自定义头 form 无法伪造)。
        """
        if request.headers.get("X-DAA-Token") != app.state.csrf_token:
            raise HTTPException(status_code=403, detail="missing/invalid CSRF token")
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


async def _http_stream(
    req: RunRequest,
    config: AgentConfig,
    client: Any,
    run_id: str,
    handlers: dict[str, WebApprovalHandler],
    artifact_dir: Path,
) -> Any:
    """HTTP wrapper around ``_stream``: emit a ``run_start`` frame carrying the
    run_id (so the browser routes its approval verdict to THIS run's handler),
    then always unregister the handler on completion/disconnect."""
    handler = handlers[run_id]
    try:
        yield _frame({"type": "run_start", "run_id": run_id})
        async for frame in _stream(req, config, client, handler, artifact_dir):
            yield frame
    finally:
        handlers.pop(run_id, None)


async def _stream(
    req: RunRequest,
    config: AgentConfig,
    client: Any,
    approval_handler: WebApprovalHandler,
    artifact_dir: Path,
) -> Any:
    """Run one agent turn and yield SSE ``data: <json>\n\n`` frames."""
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
            run_artifact_dir=None if project is not None else artifact_dir,
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
