"""Wire-shape contract tests for the SSE event codec (roadmap §P1-3.5).

Field names are frozen — the browser depends on them. These tests lock the shape
so an accidental rename is caught before it reaches the frontend.
"""

from __future__ import annotations

from data_analysis_agent.events import (
    CompleteEvent,
    ErrorEvent,
    RequestStartEvent,
    StateChangeEvent,
    StreamTextEvent,
    SystemMessageEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from data_analysis_agent.server.event_codec import encode


def test_request_start() -> None:
    assert encode(RequestStartEvent(model_id="m", turn_count=2)) == {
        "type": "request_start",
        "model_id": "m",
        "turn_count": 2,
    }


def test_stream_text() -> None:
    assert encode(StreamTextEvent(text="hi")) == {"type": "stream_text", "text": "hi"}


def test_tool_use() -> None:
    ev = ToolUseEvent(tool_use_id="t1", tool_name="data_profile", parameters={"path": "/x"})
    assert encode(ev) == {
        "type": "tool_use",
        "tool_use_id": "t1",
        "tool_name": "data_profile",
        "parameters": {"path": "/x"},
    }


def test_tool_result() -> None:
    ev = ToolResultEvent(
        tool_use_id="t1", tool_name="x", content="out", is_error=False, artifacts=("/a.png",)
    )
    assert encode(ev) == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "tool_name": "x",
        "content": "out",
        "is_error": False,
        "artifacts": ["/a.png"],
    }


def test_state_change() -> None:
    ev = StateChangeEvent(previous_state="A", new_state="B", reason="r")
    assert encode(ev) == {
        "type": "state_change",
        "previous_state": "A",
        "new_state": "B",
        "reason": "r",
    }


def test_usage() -> None:
    assert encode(UsageEvent(input_tokens=10, output_tokens=5)) == {
        "type": "usage",
        "input_tokens": 10,
        "output_tokens": 5,
    }


def test_error() -> None:
    assert encode(ErrorEvent(error=ValueError("boom"))) == {
        "type": "error",
        "error": "boom",
        "is_recoverable": False,
    }


def test_complete() -> None:
    assert encode(CompleteEvent(terminal_reason="done", final_text="ans")) == {
        "type": "complete",
        "terminal_reason": "done",
        "final_text": "ans",
    }


def test_unknown_event_falls_to_system_bucket() -> None:
    """StreamThinking/SystemMessage/ToolProgress must not be silently dropped."""
    assert encode(SystemMessageEvent(message="x")) == {
        "type": "system",
        "event": "SystemMessageEvent",
    }
