"""Telemetry: trajectory recording + feedback signals (the evolution corpus).

Leaf-ish subsystem — wired as a side channel on AgentSession, never imported by
the agent loop (decoupled via the EventConsumer protocol in events.py).
"""

from __future__ import annotations

from .feedback import (
    FeedbackRecord,
    looks_like_rephrase,
    parse_explicit_feedback,
)
from .trajectory import (
    ToolCallRecord,
    TrajectoryLogger,
    TurnRecord,
    attach_feedback_to_turns,
    load_turns,
)

__all__ = [
    "FeedbackRecord",
    "ToolCallRecord",
    "TrajectoryLogger",
    "TurnRecord",
    "attach_feedback_to_turns",
    "load_turns",
    "looks_like_rephrase",
    "parse_explicit_feedback",
]
