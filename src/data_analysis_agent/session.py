"""AgentSession: cross-turn conversation container over AgentLoop.

AgentLoop.run() executes exactly one turn; this class owns what happens
between turns — carrying history, resuming from a MessageStore, and keeping
the ledger closed so a resumed conversation is always API-valid.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .agent_loop import AgentLoop, ensure_tool_ledger_closed
from .events import AgentEvent
from .persistence import MessageStore
from .state_machine import Message
from .telemetry import FeedbackRecord, TrajectoryLogger, looks_like_rephrase


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class SessionMeta:
    """Lightweight identity for a conversation."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=_utc_now)
    title: str = ""


class AgentSession:
    """Holds conversation history across turns and runs the loop per send."""

    def __init__(
        self,
        loop: AgentLoop,
        store: MessageStore | None = None,
        meta: SessionMeta | None = None,
        trajectory_logger: TrajectoryLogger | None = None,
    ) -> None:
        self.loop = loop
        self.store = store
        self.meta = meta or SessionMeta()
        self.trajectory_logger = trajectory_logger
        self._history: list[Message] = []
        self._last_turn_monotonic: float | None = None

    def attach_feedback(self, feedback: FeedbackRecord) -> bool:
        """Record explicit /good /bad feedback against the most recent turn."""
        if self.trajectory_logger is None:
            return False
        return self.trajectory_logger.attach_feedback(feedback)

    @property
    def history(self) -> list[Message]:
        """Snapshot of the conversation so far (copy; do not mutate)."""
        return list(self._history)

    @classmethod
    def resume(cls, loop: AgentLoop, store: MessageStore) -> AgentSession:
        """Rebuild a session from a persisted JSONL store.

        The loaded history is ledger-closed: a session interrupted mid-tools
        would otherwise resume with orphan tool_use blocks and fail the next
        API call. When closure inserts synthetic results, the store is
        rewritten so disk and memory stay identical — otherwise every future
        resume re-repairs a permanently broken ledger (synthetic messages are
        positional, so an append-only patch cannot represent them).
        """
        session = cls(loop, store)
        loaded = store.load_all()
        closed = ensure_tool_ledger_closed(loaded)
        if closed != loaded:
            store.rewrite(closed)  # atomic: crash mid-repair keeps the old file
        session._history = closed
        return session

    async def send(self, user_input: str) -> AsyncGenerator[AgentEvent, None]:
        """Run one turn with full history; events stream through unchanged.

        The history write-back lives in ``finally`` and the inner generator is
        closed explicitly: a consumer that breaks out of the event stream
        (Ctrl-C, shutdown) must still trigger AgentLoop.run()'s ledger closure
        and must not leave this session holding a stale history.
        """
        if not self.meta.title:
            self.meta.title = user_input[:80]
        logger = self.trajectory_logger
        if logger is not None:
            # All telemetry interaction is suppressed: it is a side channel and
            # must never break the turn (symmetric with the per-event guard below).
            with contextlib.suppress(Exception):
                # Implicit rephrase: a fast negating follow-up flags the PREVIOUS
                # turn as unsatisfactory (collected only; consumed under review).
                if self._last_turn_monotonic is not None and looks_like_rephrase(
                    user_input, time.monotonic() - self._last_turn_monotonic
                ):
                    logger.attach_feedback(FeedbackRecord(kind="rephrase", implicit=True))
                logger.begin_turn(user_input)
        stream = self.loop.run(user_input, history=self._history)
        try:
            async for event in stream:
                if logger is not None:
                    # Telemetry is a strict side channel: a logging failure must
                    # never break the user-visible event stream.
                    with contextlib.suppress(Exception):
                        logger(event)
                yield event
        finally:
            await stream.aclose()  # runs loop.run()'s finally (ledger closure)
            self._history = list(self.loop.last_final_messages)
            if logger is not None:
                with contextlib.suppress(Exception):
                    logger.end_turn()
                self._last_turn_monotonic = time.monotonic()
