"""Stable SSE event codec for the Web workbench.

Maps each ``AgentEvent`` subclass to a plain dict for ``data: <json>`` SSE frames.
This wire shape is the contract the browser depends on (roadmap §P1-3.5): field
names are frozen, new fields are additive only.
"""

from __future__ import annotations

from typing import Any

from ..events import (
    AgentEvent,
    CompleteEvent,
    ErrorEvent,
    RequestStartEvent,
    StateChangeEvent,
    StreamTextEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)


def encode(event: AgentEvent) -> dict[str, Any]:
    """Return the SSE wire dict for one agent event."""
    if isinstance(event, RequestStartEvent):
        return {
            "type": "request_start",
            "model_id": event.model_id,
            "turn_count": event.turn_count,
        }
    if isinstance(event, StreamTextEvent):
        return {"type": "stream_text", "text": event.text}
    if isinstance(event, ToolUseEvent):
        return {
            "type": "tool_use",
            "tool_use_id": event.tool_use_id,
            "tool_name": event.tool_name,
            "parameters": dict(event.parameters),
        }
    if isinstance(event, ToolResultEvent):
        return {
            "type": "tool_result",
            "tool_use_id": event.tool_use_id,
            "tool_name": event.tool_name,
            "content": event.content,
            "is_error": event.is_error,
            "artifacts": list(event.artifacts),
        }
    if isinstance(event, StateChangeEvent):
        return {
            "type": "state_change",
            "previous_state": event.previous_state,
            "new_state": event.new_state,
            "reason": event.reason,
        }
    if isinstance(event, UsageEvent):
        return {
            "type": "usage",
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
        }
    if isinstance(event, ErrorEvent):
        return {
            "type": "error",
            "error": str(event.error),
            "is_recoverable": event.is_recoverable,
        }
    if isinstance(event, CompleteEvent):
        return {
            "type": "complete",
            "terminal_reason": event.terminal_reason,
            "final_text": event.final_text,
        }
    # StreamThinking / ToolProgress / SystemMessage / future events: a generic
    # bucket so the browser never silently drops an event type it doesn't know.
    return {"type": "system", "event": type(event).__name__}
