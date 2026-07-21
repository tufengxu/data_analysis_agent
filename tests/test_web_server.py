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
    r = client.post("/api/run/stream", json={"query": "hi", "paths": ["/data/x"]})
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
    r = client.post("/api/run/stream", json={"query": "hi", "paths": ["/data/dummy.csv"]})

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
    r = client.post("/api/run/stream", json={"query": "hi", "paths": []})
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
    r = client.post("/api/run/stream", json={"query": "hi", "paths": ["", "   "]})
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
    r = client.post("/api/run/stream", json={"query": "hi", "paths": ["/data/dummy.csv"]})
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
    )
    assert r.status_code == 200
    assert 'data: {"type": "error"' in r.text
    assert "no_such_project" in r.text
