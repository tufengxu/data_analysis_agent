"""State machine definitions for the AgentLoop.

Mirrors Claude Code's implicit state machine design:
- AgentSessionState for macro lifecycle
- TurnState for per-turn pipeline stages
- AgentState as the immutable cross-iteration state container
- ContinueReason for 7 continue paths
- Terminal for 10 termination reasons
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Any


class AgentSessionState(Enum):
    """Macro lifecycle state of an agent session."""

    IDLE = auto()
    THINKING = auto()  # Streaming model response
    TOOL_CALLING = auto()  # Dispatching tool calls
    AWAITING_CONFIRMATION = auto()  # Permission gate blocked, waiting for user
    COMPLETE = auto()
    ERROR = auto()
    INTERRUPTED = auto()


class TurnState(Enum):
    """Sub-state inside a single turn (model call -> tool exec -> result processing)."""

    INIT = auto()
    CONTEXT_BUILDING = auto()
    MODEL_CALLING = auto()
    TOOL_EXECUTING = auto()
    RESULT_PROCESSING = auto()


class ContinueReason(Enum):
    """Why the loop continued instead of terminating."""

    NEXT_TURN = auto()  # Normal continuation after tools
    COLLAPSE_DRAIN_RETRY = auto()  # 413 recovery: drain staged collapses
    REACTIVE_COMPACT_RETRY = auto()  # 413 recovery: fork sub-agent to summarize
    MAX_OUTPUT_TOKENS_ESCALATE = auto()  # 8K -> 64K escalation
    MAX_OUTPUT_TOKENS_RECOVERY = auto()  # Inject recovery message after 64K truncate
    STOP_HOOK_BLOCKING = auto()  # Stop hooks vetoed termination
    TOKEN_BUDGET_CONTINUATION = auto()  # Nudge model to continue within budget


class TerminalReason(Enum):
    """Why the loop terminated."""

    COMPLETED = auto()
    MAX_TURNS = auto()
    ABORTED_STREAMING = auto()
    ABORTED_TOOLS = auto()
    PROMPT_TOO_LONG = auto()
    MODEL_ERROR = auto()
    BLOCKING_LIMIT = auto()
    STOP_HOOK_PREVENTED = auto()
    HOOK_STOPPED = auto()


@dataclass(frozen=True)
class Message:
    """Internal message representation for conversation history."""

    role: str  # "user" | "assistant"
    content: list[dict[str, Any]] | str
    is_meta: bool = False  # Meta messages (e.g., recovery nudges) not shown to user

    def to_anthropic_format(self) -> dict[str, Any]:
        """Convert to Anthropic Messages API format."""
        return {"role": self.role, "content": self.content}


@dataclass
class ToolUseContext:
    """Cross-session tool execution context (abort signals, pending calls, etc.)."""

    pending_tool_use_ids: set[str] = field(default_factory=set)
    aborted: bool = False


@dataclass
class AgentState:
    """Immutable cross-iteration state container.

    Pattern: state.with_x(new_value) returns a new instance.
    This mirrors Claude Code's TypeScript: state = { ...state, field: newValue }
    """

    messages: list[Message] = field(default_factory=list)
    tool_use_context: ToolUseContext = field(default_factory=ToolUseContext)
    turn_count: int = 1
    transition: ContinueReason | None = None
    max_output_tokens_override: int | None = None
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    stop_hook_active: bool = False

    # --- Immutable update helpers ---

    def with_messages(self, messages: list[Message]) -> AgentState:
        return self._replace(messages=messages)

    def with_turn_count(self, turn_count: int) -> AgentState:
        return self._replace(turn_count=turn_count)

    def with_transition(self, transition: ContinueReason | None) -> AgentState:
        return self._replace(transition=transition)

    def with_max_output_tokens_override(self, value: int | None) -> AgentState:
        return self._replace(max_output_tokens_override=value)

    def with_max_output_tokens_recovery_count(self, value: int) -> AgentState:
        return self._replace(max_output_tokens_recovery_count=value)

    def with_has_attempted_reactive_compact(self, value: bool) -> AgentState:
        return self._replace(has_attempted_reactive_compact=value)

    def with_stop_hook_active(self, value: bool) -> AgentState:
        return self._replace(stop_hook_active=value)

    def with_tool_use_context(self, ctx: ToolUseContext) -> AgentState:
        return self._replace(tool_use_context=ctx)

    def _replace(self, **kwargs: Any) -> AgentState:
        """Return a new state with specified fields replaced."""
        return replace(self, **kwargs)
