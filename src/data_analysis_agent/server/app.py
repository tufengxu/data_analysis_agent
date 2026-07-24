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
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ..config import AgentConfig
from ..runtime import AgentRuntime
from .event_codec import encode

_STATIC_DIR = Path(__file__).parent / "static"


class RunRequest(BaseModel):
    """One analysis request from the browser."""

    query: str
    paths: list[str] = []  # authorized data files/dirs (absolute)
    project: str | None = None  # optional project id to run inside


def create_app(
    config: AgentConfig | None = None,
    *,
    client: Any = None,
) -> FastAPI:
    """Build the workbench app. ``client`` lets tests inject a fake LLM client."""
    config = config or AgentConfig.from_env()
    app = FastAPI(title="DataAnalysisAgent Workbench", version="0.1.0")
    app.state.config = config
    app.state.client = client

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.post("/api/run/stream")
    async def run_stream(req: RunRequest) -> StreamingResponse:
        if not config.api_key:
            raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY not set")
        return StreamingResponse(
            _stream(req, config, client),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


async def _stream(req: RunRequest, config: AgentConfig, client: Any) -> Any:
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
            config, client=client, analysis_paths=paths, project=project
        )
        async for event in runtime.session.send(req.query):
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
