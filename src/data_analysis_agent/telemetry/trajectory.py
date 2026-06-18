"""Trajectory recording: the raw material for all self-evolution.

TrajectoryLogger implements EventConsumer (events.py) and is wired as a *side
channel* on AgentSession.send() — it observes the event stream and never alters
it, so the agent loop stays untouched. One JSONL file per session under the
configured trajectories dir; one TurnRecord per send().
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..context.compression import estimate_tokens
from ..events import (
    AgentEvent,
    CompleteEvent,
    RequestStartEvent,
    StreamTextEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from ..jsonl_store import JsonlStore
from .feedback import FeedbackRecord

_DIGEST_CHARS = 2000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ToolCallRecord:
    """One tool invocation within a turn."""

    name: str
    is_error: bool
    duration_ms: int
    result_chars: int


@dataclass
class TurnRecord:
    """The full trace of one session.send() — schema is stable, evolved carefully."""

    session_id: str
    turn_id: str
    ts_start: str
    ts_end: str
    user_input: str
    active_skill: str | None
    tool_calls: list[ToolCallRecord]
    terminal_reason: str
    model_turns: int  # tool-iteration count (last RequestStart turn_count), not model calls
    tokens: dict[str, object]  # {input, output, estimated: bool}
    final_text_digest: str
    feedback: FeedbackRecord | None = None


class TrajectoryLogger:
    """Stateful EventConsumer: accumulates one turn, flushes a TurnRecord."""

    def __init__(
        self,
        trajectories_dir: str | Path,
        session_id: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.dir = Path(trajectories_dir)
        self.session_id = session_id
        self._monotonic = monotonic
        self._store = JsonlStore(self.dir / f"{session_id}.jsonl")
        self.path = self._store.path
        self._last_turn_id: str | None = None
        self._reset()

    def _reset(self) -> None:
        self._active = False
        self._turn_id = ""
        self._user_input = ""
        self._ts_start = ""
        self._active_skill: str | None = None
        self._tool_calls: list[ToolCallRecord] = []
        self._tool_starts: dict[str, tuple[str, float]] = {}
        self._model_turns = 0
        self._terminal = "UNKNOWN"
        self._final_text = ""
        self._input_tokens = 0
        self._output_tokens = 0
        self._saw_usage = False

    # --- lifecycle -------------------------------------------------------

    def begin_turn(self, user_input: str, *, turn_id: str | None = None) -> str:
        self._reset()
        self._active = True
        self._turn_id = turn_id or uuid.uuid4().hex[:12]
        self._user_input = user_input
        self._ts_start = _utc_now()
        return self._turn_id

    def __call__(self, event: AgentEvent) -> None:
        """EventConsumer entry point: accumulate turn state from the stream."""
        if not self._active:
            return
        if isinstance(event, RequestStartEvent):
            self._model_turns = event.turn_count
            if event.active_skill is not None:
                self._active_skill = event.active_skill
        elif isinstance(event, UsageEvent):
            self._saw_usage = True
            self._input_tokens += event.input_tokens
            self._output_tokens += event.output_tokens
        elif isinstance(event, ToolUseEvent):
            self._tool_starts[event.tool_use_id] = (event.tool_name, self._monotonic())
        elif isinstance(event, ToolResultEvent):
            name, started = self._tool_starts.pop(
                event.tool_use_id, (event.tool_name, self._monotonic())
            )
            self._tool_calls.append(
                ToolCallRecord(
                    name=name or event.tool_name,
                    is_error=event.is_error,
                    duration_ms=int(max(0.0, self._monotonic() - started) * 1000),
                    result_chars=len(event.content),
                )
            )
        elif isinstance(event, StreamTextEvent):
            self._final_text += event.text
        elif isinstance(event, CompleteEvent):
            self._terminal = event.terminal_reason
            if event.final_text:
                self._final_text = event.final_text

    def end_turn(self, feedback: FeedbackRecord | None = None) -> TurnRecord:
        """Build, persist, and return the TurnRecord for the current turn."""
        if self._saw_usage:
            tokens: dict[str, object] = {
                "input": self._input_tokens,
                "output": self._output_tokens,
                "estimated": False,
            }
        else:
            # Streaming usage unavailable — fall back to a char-based estimate,
            # flagged so downstream cost analysis knows not to trust it exactly.
            tokens = {
                "input": estimate_tokens(self._user_input),
                "output": estimate_tokens(self._final_text),
                "estimated": True,
            }
        record = TurnRecord(
            session_id=self.session_id,
            turn_id=self._turn_id,
            ts_start=self._ts_start,
            ts_end=_utc_now(),
            user_input=self._user_input,
            active_skill=self._active_skill,
            tool_calls=list(self._tool_calls),
            terminal_reason=self._terminal,
            model_turns=self._model_turns,
            tokens=tokens,
            final_text_digest=self._final_text[:_DIGEST_CHARS],
            feedback=feedback,
        )
        self._flush(record)
        self._last_turn_id = record.turn_id
        self._reset()
        return record

    def attach_feedback(self, feedback: FeedbackRecord) -> bool:
        """Append a feedback row referencing the most recently flushed turn.

        Explicit /good /bad arrives after the turn completed, so it is recorded
        as a separate line keyed by turn_id rather than rewriting history.
        """
        if self._last_turn_id is None:
            return False
        line = {"type": "feedback", "turn_id": self._last_turn_id, **asdict(feedback)}
        return self._store.append(line)

    # --- persistence -----------------------------------------------------

    def _flush(self, record: TurnRecord) -> bool:
        return self._store.append({"type": "turn", **asdict(record)})


def load_turns(path: str | Path) -> list[dict[str, object]]:
    """Read back the ``turn`` rows of a trajectory file (skips feedback rows)."""
    return [
        row for row in JsonlStore(path, ensure_parent=False).read() if row.get("type") == "turn"
    ]


def attach_feedback_to_turns(turns: list[dict[str, object]], path: str | Path) -> None:
    """Merge ``feedback`` rows back onto their turns (by turn_id), in place."""
    by_id = {t.get("turn_id"): t for t in turns}
    for obj in JsonlStore(path, ensure_parent=False).read():
        if obj.get("type") == "feedback":
            turn = by_id.get(obj.get("turn_id"))
            if turn is not None:
                turn["feedback"] = {
                    "kind": obj.get("kind"),
                    "detail": obj.get("detail", ""),
                    "implicit": obj.get("implicit", False),
                }


__all__ = [
    "ToolCallRecord",
    "TrajectoryLogger",
    "TurnRecord",
    "attach_feedback_to_turns",
    "load_turns",
]
