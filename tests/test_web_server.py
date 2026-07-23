"""Smoke tests for the live-agent workbench server (Wave 2, Slice 1).

The SSE endpoint is exercised by injecting a fake runtime (monkeypatching
``AgentRuntime.from_config``) so the test never touches the LLM. This locks the
contract that the server iterates ``session.send`` and frames each event with the
codec through Complete.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.config import AgentConfig
from data_analysis_agent.events import (
    CompleteEvent,
    RequestStartEvent,
    StreamTextEvent,
    ToolUseEvent,
)


class _FakeSession:
    def __init__(self, events: list) -> None:
        self._events = events

    def send(self, query: str) -> Any:
        async def gen() -> Any:
            for ev in self._events:
                yield ev

        return gen()


class _FakeRuntime:
    def __init__(self, events: list) -> None:
        self.session = _FakeSession(events)
        self.shutdown_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


def _config() -> AgentConfig:
    return AgentConfig(
        api_key="x", persistent_kernel=False, enable_telemetry=False, enable_memory=False
    )


def _csrf_headers(client: TestClient) -> dict[str, str]:
    """Pull the CSRF token out of the served index and echo it as the guard header.

    The real UI does exactly this (token embedded in HTML → X-DAA-Token header).
    Getting it from the served page (not a test-side constant) proves the server
    actually injects a token and that a same-origin caller can obtain it.
    """
    html = client.get("/").text
    marker = 'const CSRF = "'
    token = html.split(marker, 1)[1].split('"', 1)[0]
    return {"X-DAA-Token": token}


def test_index_serves_html() -> None:
    from data_analysis_agent.server.app import create_app

    client = TestClient(create_app(_config()))
    r = client.get("/")
    assert r.status_code == 200
    assert "Workbench" in r.text


def test_run_stream_400_when_no_api_key() -> None:
    """Missing API key → a clean 400, not a mid-stream SDK error frame."""
    from data_analysis_agent.server.app import create_app

    config = AgentConfig(
        api_key="", persistent_kernel=False, enable_telemetry=False, enable_memory=False
    )
    client = TestClient(create_app(config))
    r = client.post(
        "/api/run/stream", json={"query": "hi", "paths": ["/data/x"]}, headers=_csrf_headers(client)
    )
    assert r.status_code == 400


def test_run_stream_emits_sse_events_through_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import data_analysis_agent.server.app as server_app

    events = [
        RequestStartEvent(model_id="m", turn_count=1),
        StreamTextEvent(text="hello "),
        StreamTextEvent(text="world"),
        ToolUseEvent(tool_use_id="t1", tool_name="data_profile", parameters={}),
        CompleteEvent(terminal_reason="done", final_text="hello world"),
    ]
    fake = _FakeRuntime(events)

    def fake_from_config(config: AgentConfig, **kw: Any) -> _FakeRuntime:
        # analysis_paths / project / client are accepted but the fake ignores them.
        return fake

    monkeypatch.setattr(server_app.AgentRuntime, "from_config", fake_from_config)

    client = TestClient(server_app.create_app(_config()))
    r = client.post(
        "/api/run/stream",
        json={"query": "hi", "paths": ["/data/dummy.csv"]},
        headers=_csrf_headers(client),
    )

    assert r.status_code == 200
    body = r.text
    assert 'data: {"type": "request_start"' in body
    assert 'data: {"type": "stream_text", "text": "hello "' in body
    assert 'data: {"type": "tool_use"' in body
    assert 'data: {"type": "complete"' in body
    # Frames are SSE (blank-line separated).
    assert body.count("\n\n") >= 5
    # The runtime is always shut down, even on the success path.
    assert fake.shutdown_called is True


def test_run_stream_requires_authorized_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """No paths → fail closed (never default to the server's cwd)."""
    import data_analysis_agent.server.app as server_app

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        server_app.AgentRuntime,
        "from_config",
        lambda config, **kw: calls.append(kw) or _FakeRuntime([]),
    )
    client = TestClient(server_app.create_app(_config()))
    r = client.post(
        "/api/run/stream", json={"query": "hi", "paths": []}, headers=_csrf_headers(client)
    )
    assert r.status_code == 200
    assert "no authorized data paths" in r.text
    assert 'data: {"type": "error"' in r.text
    assert calls == []  # runtime never built


def test_run_stream_rejects_blank_path_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """`paths=[""]` must not reach the tools — Path('') resolves to cwd."""
    import data_analysis_agent.server.app as server_app

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        server_app.AgentRuntime,
        "from_config",
        lambda config, **kw: calls.append(kw) or _FakeRuntime([]),
    )
    client = TestClient(server_app.create_app(_config()))
    r = client.post(
        "/api/run/stream", json={"query": "hi", "paths": ["", "   "]}, headers=_csrf_headers(client)
    )
    assert "no authorized data paths" in r.text
    assert calls == []


def test_run_stream_with_real_runtime_and_fake_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration: a REAL from_config (fake LLM client) streams through complete.

    This is the test the smoke test can't be: it proves the server wires the real
    composition root (same runtime as the CLI), not a stub.
    """
    from data_analysis_agent.protocol.messages import ModelResponse, TextBlock
    from data_analysis_agent.server.app import create_app

    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))

    class _SeqClient:
        model = "dummy"

        def __init__(self, responses: list) -> None:
            self.responses = list(responses)

        async def stream_model(
            self, messages, system=None, tools=None, max_tokens=None, tool_choice=None
        ):
            for resp in self.responses:
                for block in resp.content:
                    yield block
                yield resp

    config = AgentConfig(
        api_key="x", persistent_kernel=False, enable_telemetry=False, enable_memory=False
    )
    seq = _SeqClient([ModelResponse(content=[TextBlock("hello world")], stop_reason="end_turn")])
    client = TestClient(create_app(config, client=seq))
    r = client.post(
        "/api/run/stream",
        json={"query": "hi", "paths": ["/data/dummy.csv"]},
        headers=_csrf_headers(client),
    )
    assert r.status_code == 200
    assert "hello world" in r.text
    assert 'data: {"type": "complete"' in r.text


def test_run_stream_surfaces_bad_project_as_error_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A non-existent project id must produce an error frame, not a crash."""
    import data_analysis_agent.server.app as server_app

    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    client = TestClient(server_app.create_app(_config()))
    r = client.post(
        "/api/run/stream",
        json={"query": "hi", "paths": ["/data/x"], "project": "no_such_project"},
        headers=_csrf_headers(client),
    )
    assert r.status_code == 200
    assert 'data: {"type": "error"' in r.text
    assert "no_such_project" in r.text


def test_run_stream_and_approval_require_csrf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same-origin agent endpoints are CSRF-guarded: no/mismatched X-DAA-Token → 403.

    This is the control that stops a same-origin artifact page (or any cross-site
    form) from silently driving the agent run or a pending approval (review HIGH #1).
    """
    import data_analysis_agent.server.app as server_app

    monkeypatch.setattr(
        server_app.AgentRuntime, "from_config", lambda config, **kw: _FakeRuntime([])
    )
    client = TestClient(server_app.create_app(_config()))

    # No token at all → 403.
    assert (
        client.post("/api/run/stream", json={"query": "hi", "paths": ["/data/x"]}).status_code
        == 403
    )
    assert client.post("/api/approval", json={"approved": True}).status_code == 403
    # Wrong token → 403 (the served token is the only accepted one).
    bad = {"X-DAA-Token": "not-the-server-token"}
    assert (
        client.post(
            "/api/run/stream", json={"query": "hi", "paths": ["/data/x"]}, headers=bad
        ).status_code
        == 403
    )
    assert client.post("/api/approval", json={"approved": True}, headers=bad).status_code == 403
    # The token the server actually served IS accepted (control reaches the handler).
    good = _csrf_headers(client)
    assert (
        client.post(
            "/api/run/stream", json={"query": "hi", "paths": ["/data/x"]}, headers=good
        ).status_code
        == 200
    )


# ----------------------------- 统一 workbench(server 挂 web,#30) -----------------------------


def test_report_workbench_mounted_under_workbench(tmp_path: Path) -> None:
    """One app serves BOTH live run and report workbench (single-port workbench)."""
    from data_analysis_agent.server.app import create_app

    client = TestClient(create_app(_config(), artifact_dir=tmp_path))
    r = client.get("/workbench/")
    assert r.status_code == 200
    assert "Workbench" in r.text


def test_report_endpoints_reachable_through_server(tmp_path: Path) -> None:
    """web 的报告/QA/反馈端点经 server 单一进程可达(前缀 /workbench)。"""
    from data_analysis_agent.server.app import create_app

    client = TestClient(create_app(_config(), artifact_dir=tmp_path))
    contract = client.post("/workbench/api/report/contract", json={"question": "上周销售日报"})
    assert contract.status_code == 200
    assert contract.json()["report_type"] == "daily_kpi"

    fb = client.post("/workbench/api/feedback", json={"tags": ["good"], "comment": "ok"})
    assert fb.status_code == 200
    assert fb.json()["stored"] is True
    assert (tmp_path / "feedback.jsonl").exists()


def test_artifact_preview_reachable_and_guarded(tmp_path: Path) -> None:
    """artifact 预览经统一 app 可达且仍限 workspace/artifacts 内 .html。"""
    from data_analysis_agent.server.app import create_app

    (tmp_path / "report.html").write_text("<h1>ok</h1>", encoding="utf-8")
    client = TestClient(create_app(_config(), artifact_dir=tmp_path))
    ok = client.get("/workbench/artifacts/report.html")
    assert ok.status_code == 200
    # Served untrusted (agent) HTML runs in an opaque origin so it cannot reach the
    # workbench origin to read the CSRF token or drive run/approval (review HIGH #1).
    assert ok.headers["content-security-policy"] == "sandbox"
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert client.get("/workbench/artifacts/../secret.html").status_code == 404


# ----------------------------- 上传 + project 选择器(#24 / #31) -----------------------------


def test_upload_streams_into_project_uploads(tmp_path: Path, monkeypatch) -> None:
    """裸请求体流式上传落 project uploads/(二进制,免 multipart 依赖)。"""
    from data_analysis_agent.server.app import create_app
    from data_analysis_agent.workspace import Project

    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    Project.init("p1")
    client = TestClient(create_app(_config()))
    r = client.post(
        "/api/upload?project=p1&filename=data.csv",
        content=b"a,b\n1,2\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "data.csv"
    assert body["size"] == 8
    assert (tmp_path / "daa/projects/p1/uploads/data.csv").read_bytes() == b"a,b\n1,2\n"


def test_upload_rejects_bad_extension(tmp_path: Path, monkeypatch) -> None:
    from data_analysis_agent.server.app import create_app
    from data_analysis_agent.workspace import Project

    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    Project.init("p1")
    client = TestClient(create_app(_config()))
    r = client.post(
        "/api/upload?project=p1&filename=evil.exe",
        content=b"x",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 400


def test_upload_rejects_traversal_and_unknown_project(tmp_path: Path, monkeypatch) -> None:
    from data_analysis_agent.server.app import create_app
    from data_analysis_agent.workspace import Project

    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    Project.init("p1")
    client = TestClient(create_app(_config()))
    r = client.post(
        "/api/upload?project=p1&filename=../evil.csv",
        content=b"x",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 400
    r2 = client.post(
        "/api/upload?project=nope&filename=d.csv",
        content=b"x",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r2.status_code == 404


def test_list_projects(tmp_path: Path, monkeypatch) -> None:
    from data_analysis_agent.server.app import create_app
    from data_analysis_agent.workspace import Project

    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    Project.init("alpha")
    Project.init("beta")
    client = TestClient(create_app(_config()))
    ids = [p["project_id"] for p in client.get("/api/projects").json()["projects"]]
    assert ids == ["alpha", "beta"]


# ----------------------------- 审批通道(P1-3.7 / #27) -----------------------------


def test_approval_full_flow_approve(tmp_path: Path, monkeypatch) -> None:
    """local_safe mutator → 挂起 AWAITING_CONFIRMATION(帧带 additive tool_name/params)
    → 异线程 resolve(同真实 /api/approval 从不同线程唤醒)→ 继续执行至 complete。

    直接驱动 ``_stream``(不经 TestClient):后者在流挂起时缓冲帧,测不到 AWAITING。
    """
    import threading

    import anyio

    from data_analysis_agent.protocol.messages import ModelResponse, TextBlock, ToolUseBlock
    from data_analysis_agent.server.app import RunRequest, _stream
    from data_analysis_agent.server.approval import WebApprovalHandler

    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))

    class _Seq:
        model = "dummy"

        def __init__(self, responses):
            self.responses = list(responses)

        async def stream_model(
            self, messages, system=None, tools=None, max_tokens=None, tool_choice=None
        ):
            resp = self.responses.pop(0)  # one response per turn
            for block in resp.content:
                yield block
            yield resp

    cfg = AgentConfig(
        api_key="x",
        persistent_kernel=False,
        enable_telemetry=False,
        enable_memory=False,
        permission_preset="local_safe",
    )
    seq = _Seq(
        [
            ModelResponse(
                content=[
                    ToolUseBlock(id="t1", name="python_analysis", input={"code": "import pandas"})
                ],
                stop_reason="tool_use",
            ),
            ModelResponse(content=[TextBlock("done")], stop_reason="end_turn"),
        ]
    )
    handler = WebApprovalHandler()
    frames: list[str] = []
    resolved: list[bool] = []

    async def consume() -> None:
        async for frame in _stream(
            RunRequest(query="hi", paths=["/data/x.csv"]), cfg, seq, handler, tmp_path / "artifacts"
        ):
            frames.append(frame)
            if "AWAITING_CONFIRMATION" in frame and not resolved:
                resolved.append(True)
                # /api/approval 是从 HTTP 请求线程唤醒 agent 循环的;同样从异线程 resolve。
                threading.Timer(0.05, lambda: handler.resolve(True)).start()

    async def main() -> None:
        with anyio.move_on_after(15):
            await consume()

    anyio.run(main)

    body = "".join(frames)
    # 挂起帧带 additive 字段(wire 契约只增)
    assert "AWAITING_CONFIRMATION" in body
    assert '"tool_name": "python_analysis"' in body
    # 批准后继续:回到 TOOL_CALLING → tool_result → complete
    assert "approved" in body
    assert '"type": "tool_result"' in body
    assert '"type": "complete"' in body


def test_approval_timeout_defaults_to_deny() -> None:
    """超时 = deny(fail-closed 硬约束):handler 无人裁决时返回 False。"""
    import asyncio

    from data_analysis_agent.server import approval as approval_mod
    from data_analysis_agent.server.approval import WebApprovalHandler

    approval_mod.APPROVAL_TIMEOUT_S = 0.05  # 缩短超时以便测试
    handler = WebApprovalHandler()
    decision = asyncio.run(handler("python_analysis", {"code": "x"}))
    assert decision is False
    assert handler.pending is None


def test_approval_resolve_without_pending_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """无 pending 决定时 /api/approval 不得误判 resolved(fail-closed)。"""
    from data_analysis_agent.server.app import create_app

    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    client = TestClient(create_app(_config()))
    res = client.post("/api/approval", json={"approved": True}, headers=_csrf_headers(client))
    assert res.json()["resolved"] is False
