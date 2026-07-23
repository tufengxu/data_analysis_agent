"""Web approval handler for the SSE workbench (P1-3.7 / #27).

When the agent's permission gate returns ASK, the loop yields an
``AWAITING_CONFIRMATION`` state change and awaits this handler. The handler stashes
the pending decision, tags the already-yielded state-change frame with the tool
name + parameters (additive wire fields), and blocks until the browser POSTs a
verdict to ``/api/approval`` — or the timeout elapses, which is a fail-closed DENY.

The server keeps ``/api/run/stream``'s signature frozen by binding the handler per
request inside ``_stream`` (see server/app.py).
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

# Default wait for a human verdict; on expiry the decision is DENY (fail-closed).
APPROVAL_TIMEOUT_S = 120.0


class WebApprovalHandler:
    """``ApprovalHandler`` impl: bridges the agent loop to the browser modal."""

    def __init__(self) -> None:
        self.pending: dict[str, Any] | None = None
        self._decision = False
        # threading.Event has no loop/thread affinity, so resolve() (called from the
        # HTTP request's thread/loop) safely wakes the agent loop awaiting __call__ —
        # even across different event loops (TestClient portal vs. request loop) and
        # on Python 3.13 where asyncio.Event no longer exposes its loop.
        self._done = threading.Event()

    async def __call__(self, tool_name: str, params: dict[str, Any]) -> bool:
        # Single-run: only one AWAITING_CONFIRMATION is in flight at a time.
        self.pending = {"tool_name": tool_name, "parameters": params}
        self._decision = False
        self._done.clear()
        deadline = time.monotonic() + APPROVAL_TIMEOUT_S
        try:
            # Poll the threading.Event without blocking the event loop.
            while not self._done.is_set():
                if time.monotonic() >= deadline:
                    raise TimeoutError
                await asyncio.sleep(0.02)
        except TimeoutError:
            self._decision = False  # 超时 = deny(硬约束)
        finally:
            decision = self._decision
            self.pending = None
            self._done.clear()
        return decision

    def resolve(self, approved: bool) -> bool:
        """Record the browser's verdict; False if no decision is pending."""
        if self.pending is None:
            return False
        self._decision = approved
        self._done.set()
        return True


def approval_ui(handler: WebApprovalHandler) -> Any:
    """Wrap an async event stream so AWAITING_CONFIRMATION frames carry the
    pending tool_name + parameters (additive) for the browser modal."""

    async def gen(stream: Any) -> Any:
        from ..events import StateChangeEvent

        async for event in stream:
            if (
                isinstance(event, StateChangeEvent)
                and event.new_state == "AWAITING_CONFIRMATION"
                and handler.pending is not None
            ):
                # StateChangeEvent is a frozen dataclass; set the transient payload
                # via object.__setattr__ (kept out of the wire dataclass itself).
                object.__setattr__(event, "approval_payload", dict(handler.pending))
            yield event

    return gen
