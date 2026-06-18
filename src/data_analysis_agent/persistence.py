"""Message history persistence using JSONL format.

Supports append-only storage, session resume, and fork. The JSONL mechanism
(atomic rewrite, read tolerance, writable-dir degradation) lives in JsonlStore;
this module owns only the Message ⇄ row mapping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .jsonl_store import JsonlStore
from .state_machine import Message


def _utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp with a stable Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_row(message: Message) -> dict[str, object]:
    return {
        "timestamp": _utc_timestamp(),
        "role": message.role,
        "content": message.content,
        "is_meta": message.is_meta,
    }


class MessageStore:
    """Append-only JSONL message store (Message mapping over JsonlStore)."""

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)
        self._store = JsonlStore(self.file_path)

    def append(self, message: Message) -> None:
        """Append a single message to the store."""
        self._store.append(_to_row(message))

    def append_batch(self, messages: list[Message]) -> None:
        """Append multiple messages."""
        self._store.extend(_to_row(m) for m in messages)

    def rewrite(self, messages: list[Message]) -> None:
        """Atomically replace the store contents.

        Used for ledger repairs (positional synthetic tool_results cannot be
        represented by appends).
        """
        self._store.rewrite(_to_row(m) for m in messages)

    def load_all(self) -> list[Message]:
        """Load all messages; rows missing required fields are skipped."""
        messages: list[Message] = []
        for record in self._store.read():
            try:
                messages.append(
                    Message(
                        role=record["role"],  # type: ignore[arg-type]
                        content=record["content"],  # type: ignore[arg-type]
                        is_meta=bool(record.get("is_meta", False)),
                    )
                )
            except KeyError:
                continue
        return messages

    def load_last_n(self, n: int) -> list[Message]:
        """Load the last N messages."""
        all_messages = self.load_all()
        return all_messages[-n:] if n < len(all_messages) else all_messages

    def fork(self, new_path: str | Path, last_n: int | None = None) -> MessageStore:
        """Create a forked store with optional last N messages."""
        new_store = MessageStore(new_path)
        messages = self.load_all()
        if last_n is not None:
            messages = messages[-last_n:]
        new_store.append_batch(messages)
        return new_store

    def clear(self) -> None:
        """Clear the store."""
        self._store.clear()

    def __len__(self) -> int:
        """Return the number of messages in the store."""
        return self._store.count()
