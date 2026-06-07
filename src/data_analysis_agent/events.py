"""Event stream system for the agent loop.

Translates Claude Code's AsyncGenerator pattern into Python's async generator model.
All execution progress is exposed as typed events consumed by subscribers.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class EventType(Enum):
    """Discriminated union tag for agent events."""

    STREAM_TEXT = auto()  # Text delta from model streaming
    STREAM_THINKING = auto()  # Thinking delta from model
    TOOL_USE = auto()  # Model requested a tool call
    TOOL_RESULT = auto()  # Tool execution completed
    TOOL_PROGRESS = auto()  # Real-time tool progress (e.g., streaming bash output)
    STATE_CHANGE = auto()  # Session-level state transition
    SYSTEM_MESSAGE = auto()  # System notification (compact boundary, etc.)
    REQUEST_START = auto()  # New API request initiated
    TOMBSTONE = auto()  # Synthetic tool_result for orphan tool_use
    ERROR = auto()  # Recoverable or terminal error
    COMPLETE = auto()  # Terminal: normal completion


@dataclass(frozen=True)
class AgentEvent:
    """Base class for all agent events."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ms: float = field(default_factory=lambda: __import__("time").time() * 1000)

    def get_event_type(self) -> EventType:
        raise NotImplementedError


@dataclass(frozen=True)
class StreamTextEvent(AgentEvent):
    """Incremental text fragment from model streaming."""

    text: str = ""
    content_block_id: str | None = None

    def get_event_type(self) -> EventType:
        return EventType.STREAM_TEXT


@dataclass(frozen=True)
class StreamThinkingEvent(AgentEvent):
    """Incremental thinking fragment from model."""

    thinking: str = ""
    content_block_id: str | None = None

    def get_event_type(self) -> EventType:
        return EventType.STREAM_THINKING


@dataclass(frozen=True)
class ToolUseEvent(AgentEvent):
    """Model emitted a tool_use block."""

    tool_use_id: str = ""
    tool_name: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    parameters_complete: bool = False

    def get_event_type(self) -> EventType:
        return EventType.TOOL_USE


@dataclass(frozen=True)
class ToolResultEvent(AgentEvent):
    """Tool execution finished."""

    tool_use_id: str = ""
    tool_name: str = ""
    content: str = ""
    is_error: bool = False
    abort_reason: str | None = None

    def get_event_type(self) -> EventType:
        return EventType.TOOL_RESULT


@dataclass(frozen=True)
class ToolProgressEvent(AgentEvent):
    """Real-time progress from a long-running tool."""

    tool_use_id: str = ""
    tool_name: str = ""
    chunk: str = ""

    def get_event_type(self) -> EventType:
        return EventType.TOOL_PROGRESS


@dataclass(frozen=True)
class StateChangeEvent(AgentEvent):
    """Session-level state transitioned."""

    previous_state: str = ""
    new_state: str = ""
    reason: str = ""

    def get_event_type(self) -> EventType:
        return EventType.STATE_CHANGE


@dataclass(frozen=True)
class RequestStartEvent(AgentEvent):
    """A new model API request started."""

    model_id: str = ""
    max_output_tokens: int = 0
    turn_count: int = 0

    def get_event_type(self) -> EventType:
        return EventType.REQUEST_START


@dataclass(frozen=True)
class SystemMessageEvent(AgentEvent):
    """System-level notification (e.g., compact boundary)."""

    message: str = ""
    is_meta: bool = True  # Meta messages are not shown to end users

    def get_event_type(self) -> EventType:
        return EventType.SYSTEM_MESSAGE


@dataclass(frozen=True)
class ErrorEvent(AgentEvent):
    """An error occurred during agent execution."""

    error: Exception = field(default_factory=Exception)
    is_recoverable: bool = False
    withheld: bool = False  # True if error is being handled internally

    def get_event_type(self) -> EventType:
        return EventType.ERROR


@dataclass(frozen=True)
class CompleteEvent(AgentEvent):
    """Terminal event: agent loop finished normally."""

    terminal_reason: str = ""
    final_text: str = ""

    def get_event_type(self) -> EventType:
        return EventType.COMPLETE


# Type alias for event consumers
EventConsumer = Callable[[AgentEvent], None]
