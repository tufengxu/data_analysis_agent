"""Message history persistence using JSONL format.

Supports append-only storage, session resume, and fork.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .state_machine import Message


def _utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp with a stable Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MessageStore:
    """Append-only JSONL message store."""

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, message: Message) -> None:
        """Append a single message to the store."""
        record = {
            "timestamp": _utc_timestamp(),
            "role": message.role,
            "content": message.content,
            "is_meta": message.is_meta,
        }
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_batch(self, messages: list[Message]) -> None:
        """Append multiple messages."""
        with open(self.file_path, "a", encoding="utf-8") as f:
            for msg in messages:
                record = {
                    "timestamp": _utc_timestamp(),
                    "role": msg.role,
                    "content": msg.content,
                    "is_meta": msg.is_meta,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_all(self) -> list[Message]:
        """Load all messages from the store."""
        messages: list[Message] = []
        if not self.file_path.exists():
            return messages

        with open(self.file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    messages.append(
                        Message(
                            role=record["role"],
                            content=record["content"],
                            is_meta=record.get("is_meta", False),
                        )
                    )
                except (json.JSONDecodeError, KeyError):
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
        if self.file_path.exists():
            self.file_path.unlink()

    def __len__(self) -> int:
        """Return the number of messages in the store."""
        count = 0
        if not self.file_path.exists():
            return 0
        with open(self.file_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count
