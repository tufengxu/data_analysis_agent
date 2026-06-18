"""JsonlStore: atomic JSONL persistence primitive (pure stdlib LEAF module).

One place for the mechanism every disk-backed store re-implemented: writable-dir
degradation, append, atomic full rewrite (tmp + os.replace), and read with
per-line + whole-file tolerance. Domain stores compose this and keep only their
domain semantics (indexing, TTL, fingerprint staleness, message mapping).

Operates on plain ``dict`` rows; dict↔domain mapping (and domain-specific
validation errors) stays in the caller, so the layering is clean:
    bytes ⇄ dict-row   →  JsonlStore
    dict-row ⇄ domain  →  the composing store
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class JsonlStore:
    """A JSONL file with atomic rewrite and graceful read-only degradation."""

    def __init__(self, path: str | Path, *, ensure_parent: bool = True) -> None:
        self._path = Path(path)
        self._available = True
        if ensure_parent:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self._available = False
                logger.warning("JsonlStore disabled (dir not writable): %s (%r)", self._path, e)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def available(self) -> bool:
        """False when the directory could not be made writable; writes no-op."""
        return self._available

    def exists(self) -> bool:
        return self._path.exists()

    # --- writes ---------------------------------------------------------

    def append(self, record: dict[str, object]) -> bool:
        """Append one row; returns False (not raises) on a write failure."""
        return self.extend((record,))

    def extend(self, records: Iterable[dict[str, object]]) -> bool:
        """Append many rows in a single open."""
        if not self._available:
            return False
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("JsonlStore append failed (%s): %r", self._path, e)
            return False
        return True

    def rewrite(self, records: Iterable[dict[str, object]]) -> bool:
        """Atomically replace the whole file (tmp + os.replace).

        A crash mid-rewrite leaves the previous file intact — the temp file is
        only swapped in once fully written.
        """
        if not self._available:
            return False
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            os.replace(tmp, self._path)
        except OSError as e:
            logger.warning("JsonlStore rewrite failed (%s): %r", self._path, e)
            return False
        return True

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("JsonlStore clear failed (%s): %r", self._path, e)

    # --- reads ----------------------------------------------------------

    def read(self) -> list[dict[str, object]]:
        """All rows as dicts, skipping blank/corrupt lines and unreadable files.

        Only JSON-shape errors are absorbed here; domain decoding (and its
        errors) belongs to the caller.
        """
        return list(self.iter_rows())

    def iter_rows(self) -> Iterator[dict[str, object]]:
        """Yield rows lazily — line-streaming (low memory) with read tolerance.

        Streams the file rather than slurping it, so a large history (long
        sessions) is not held in memory at 2x. An unreadable file (perms/bad
        bytes) degrades to whatever was read so far, never crashing recovery.
        """
        if not self._path.exists():
            return
        try:
            with self._path.open(encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        yield obj
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("JsonlStore could not read %s: %r", self._path, e)

    def count(self) -> int:
        """Number of non-blank lines (cheap; does not parse)."""
        if not self._path.exists():
            return 0
        try:
            with self._path.open(encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except OSError:
            return 0
