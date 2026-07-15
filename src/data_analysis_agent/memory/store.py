"""MemoryStore: JSONL-backed store for textual domain memory (3 kinds).

Keyword + recency retrieval, no vector index — at the scale of tens-to-hundreds
of entries that would be over-engineering (decision recorded in the report).
Keyed by (kind, key) upsert so re-stating a metric updates it in place.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ..jsonl_store import JsonlStore
from .model import CONFIRM_AFTER_USES, MemoryEntry, _utc_now

_WORD = re.compile(r"[\w一-鿿]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text)}


class MemoryStore:
    """Disk-backed store of MemoryEntry, keyed by (kind, key)."""

    def __init__(
        self,
        store_dir: str | Path,
        *,
        max_entries: int = 500,
        leak_check: Callable[[str], bool] | None = None,
    ) -> None:
        self.dir = Path(store_dir)
        self.max_entries = max_entries
        # Injected (memory may not import security per the drift rules): returns
        # True if a metric's content carries a numeric value (ADR 0004 leak).
        self._leak_check = leak_check
        self._store = JsonlStore(self.dir / "memory.jsonl")
        self._index: dict[tuple[str, str], MemoryEntry] = {}
        self._load()

    def _load(self) -> None:
        for row in self._store.read():
            try:
                entry = MemoryEntry.from_dict(row)
            except (TypeError, KeyError):  # domain decoding; JSON errors absorbed by JsonlStore
                continue
            self._index[(entry.kind, entry.key)] = entry  # last write wins

    def put(self, entry: MemoryEntry) -> None:
        existing = self._index.get((entry.kind, entry.key))
        if existing is not None:
            # Preserve accumulated trust/recency on re-statement.
            entry.created_at = existing.created_at
            entry.use_count = max(entry.use_count, existing.use_count)
        # ADR 0004 leak guard: a metric carrying a numeric VALUE is never
        # auto-confirmed (see note_accepted_use). We do NOT downgrade an entry
        # the caller explicitly marked confirmed=True (e.g. /define) — that is a
        # human-stated definition, not a mined value.
        self._index[(entry.kind, entry.key)] = entry
        self._evict()
        self._rewrite()

    def get(self, kind: str, key: str) -> MemoryEntry | None:
        return self._index.get((kind, key))

    def all(self) -> list[MemoryEntry]:
        return list(self._index.values())

    def search(
        self, query: str, *, kinds: tuple[str, ...] | None = None, top_k: int = 8
    ) -> list[MemoryEntry]:
        """Relevance = key-substring + token overlap + use_count; 0 dropped.

        Substring matching is essential for CJK: there is no word segmentation,
        so a whole query like "活跃用户怎么算" is one token and would never
        token-overlap the entry key "活跃用户". Substring containment recovers it.
        """
        q_lower = query.lower()
        q_tokens = _tokens(query)
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._index.values():
            if kinds is not None and entry.kind not in kinds:
                continue
            text = (entry.key + " " + entry.content).lower()
            score = 0.0
            if entry.key and entry.key.lower() in q_lower:
                score += 3
            score += len(q_tokens & _tokens(text)) * 2
            score += sum(1 for t in q_tokens if len(t) >= 2 and t in text)
            if score == 0:
                continue
            scored.append((score + min(entry.use_count, 5), entry))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def touch(self, kind: str, key: str) -> None:
        """Mark an entry as surfaced (recency only).

        Surfacing in the prompt is NOT acceptance, so it must not advance
        confirmation (that was the old false-trust bug). The rephrase-gated
        light-confirm runs through ``note_accepted_use`` instead.
        """
        entry = self._index.get((kind, key))
        if entry is None:
            return
        entry.last_used_at = _utc_now()
        self._rewrite()

    def note_accepted_use(self, kind: str, key: str) -> None:
        """Record a use the user did NOT push back on; a metric auto-confirms
        after enough such accepted uses (ADR 0004 light-confirm, rephrase-gated).

        A mined metric carrying a numeric VALUE (ADR 0004 violation) is never
        auto-confirmed — it must wait for an explicit human confirm() so a stale
        number can't silently pin itself as established.
        """
        entry = self._index.get((kind, key))
        if entry is None:
            return
        entry.use_count += 1
        entry.last_used_at = _utc_now()
        leaky = self._leak_check(entry.content or "") if self._leak_check is not None else False
        if (
            entry.kind == "metric_definition"
            and entry.use_count >= CONFIRM_AFTER_USES
            and not leaky
        ):
            entry.confirmed = True
        self._rewrite()

    def confirm(self, kind: str, key: str) -> bool:
        entry = self._index.get((kind, key))
        if entry is None:
            return False
        entry.confirmed = True
        self._rewrite()
        return True

    def _evict(self) -> None:
        if len(self._index) <= self.max_entries:
            return
        # Drop least-recently-used beyond the cap.
        ordered = sorted(self._index.items(), key=lambda kv: kv[1].last_used_at)
        for k, _ in ordered[: len(self._index) - self.max_entries]:
            self._index.pop(k, None)

    def _rewrite(self) -> None:
        self._store.rewrite(entry.to_dict() for entry in self._index.values())
