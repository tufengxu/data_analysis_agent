"""Persistent, line-paginated store for original tool results (CCR-lite).

Pure stdlib LEAF module — no imports from data_analysis_agent — so it stays
importable by both the harness (agent_loop) and the retrieve tool without
violating the sampling dependency rule. Holds the ORIGINAL content of a tool
result that was lossily compacted, so the model can retrieve it on demand.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Page text is capped below SamplingConfig.trigger_chars (8000) so a retrieved
# page is never itself re-summarized by compact_result at the tool-result seam.
_MAX_PAGE_CHARS = 7500


@dataclass
class RetrievedPage:
    """One page of a retrieved original result."""

    result_id: str
    total_lines: int
    matched_lines: int
    offset: int
    returned_lines: int
    text: str
    truncated: bool
    tool: str


class ResultStore:
    """Disk-backed store of original tool-result content, keyed by result id."""

    def __init__(
        self,
        store_dir: Path,
        *,
        ttl_seconds: int = 3600,
        max_total_bytes: int = 64 * 1024 * 1024,
        max_entry_bytes: int = 8 * 1024 * 1024,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.dir = Path(store_dir)
        self.ttl_seconds = ttl_seconds
        self.max_total_bytes = max_total_bytes
        self.max_entry_bytes = max_entry_bytes
        self._clock = clock
        self._index: dict[str, dict[str, Any]] = {}
        self._available = True
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._load_index()
            self._evict()
        except OSError:
            self._available = False  # read-only fs -> disabled, degrade gracefully

    @property
    def index_path(self) -> Path:
        return self.dir / "index.jsonl"

    def _file_for(self, result_id: str) -> Path:
        name = hashlib.sha256(result_id.encode("utf-8")).hexdigest()[:32]
        return self.dir / f"{name}.txt"

    def _load_index(self) -> None:
        if not self.index_path.exists():
            return
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            rid = rec.get("id")
            if isinstance(rid, str):
                self._index[rid] = rec  # last write wins

    def _rewrite_index(self) -> None:
        with self.index_path.open("w", encoding="utf-8") as fh:
            for rec in self._index.values():
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _drop(self, result_id: str) -> None:
        rec = self._index.pop(result_id, None)
        if rec is not None:
            with contextlib.suppress(OSError):
                Path(rec["file"]).unlink(missing_ok=True)

    def _evict(self) -> None:
        now = self._clock()
        expired = [
            rid
            for rid, rec in self._index.items()
            if now - rec.get("created_at", 0) > self.ttl_seconds
        ]
        for rid in expired:
            self._drop(rid)
        total = sum(int(rec.get("bytes", 0)) for rec in self._index.values())
        if total > self.max_total_bytes:
            for rid, rec in sorted(self._index.items(), key=lambda kv: kv[1].get("created_at", 0)):
                if total <= self.max_total_bytes:
                    break
                total -= int(rec.get("bytes", 0))
                self._drop(rid)
        self._rewrite_index()

    def put(self, result_id: str, content: str, meta: dict[str, Any]) -> bool:
        if not self._available:
            return False
        data = content.encode("utf-8")
        if len(data) > self.max_entry_bytes:
            return False
        path = self._file_for(result_id)
        try:
            path.write_text(content, encoding="utf-8")
        except OSError:
            return False
        self._index[result_id] = {
            "id": result_id,
            "file": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "lines": content.count("\n") + 1,
            "created_at": self._clock(),
            "tool": str(meta.get("tool", "")),
        }
        self._evict()
        return result_id in self._index

    def get(
        self,
        result_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        query: str | None = None,
    ) -> RetrievedPage | None:
        if not self._available:
            return None
        rec = self._index.get(result_id)
        if rec is None:
            return None
        if self._clock() - rec.get("created_at", 0) > self.ttl_seconds:
            self._drop(result_id)
            self._rewrite_index()
            return None
        try:
            content = Path(rec["file"]).read_text(encoding="utf-8")
        except OSError:
            return None

        lines = content.split("\n")
        total = len(lines)
        if query:
            needle = query.lower()
            lines = [ln for ln in lines if needle in ln.lower()]
        matched = len(lines)
        offset = max(0, offset)
        page_lines = lines[offset : offset + max(1, limit)]
        body = "\n".join(page_lines)
        truncated = False
        if len(body) > _MAX_PAGE_CHARS:
            body = body[:_MAX_PAGE_CHARS] + "\n…[页过大已截断,缩小 limit 或用 query]"
            truncated = True
        tool = str(rec.get("tool", ""))
        query_note = f" (query={query!r} matched {matched})" if query else ""
        header = (
            f"[result_id={result_id} | lines {offset}-{offset + len(page_lines)} "
            f"of {total}{query_note} | tool={tool}]"
        )
        return RetrievedPage(
            result_id=result_id,
            total_lines=total,
            matched_lines=matched,
            offset=offset,
            returned_lines=len(page_lines),
            text=header + "\n" + body,
            truncated=truncated,
            tool=tool,
        )
