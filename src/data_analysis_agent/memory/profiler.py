"""Dataset profiler: deterministic dataset_profile generation + layered staleness.

Fully deterministic — no LLM, no misattribution risk (decision: 画像确定性全自动).
Generation reads the file header (always, via stdlib csv) for the structure
layer, and best-effort row/null stats (via pandas if present) for the
statistics layer. Staleness follows the column-fingerprint rule:

    fingerprint unchanged + mtime newer  -> stale (recompute stats, keep schema)
    fingerprint changed (add/remove col) -> invalid (rebuild whole profile)
    fingerprint unchanged + mtime same   -> fresh
"""

from __future__ import annotations

import contextlib
import csv
from pathlib import Path
from typing import Any, Literal

from ..jsonl_store import JsonlStore
from .model import DatasetProfile, _utc_now, column_fingerprint

_TABULAR_SUFFIXES = {".csv", ".tsv", ".parquet"}
_STATS_SAMPLE_ROWS = 1000

Staleness = Literal["fresh", "stale", "invalid", "missing"]


def is_tabular(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _TABULAR_SUFFIXES


def _read_columns(path: Path) -> list[str] | None:
    """Header columns via stdlib only (always available)."""
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            with path.open("r", encoding="utf-8", newline="") as fh:
                row = next(csv.reader(fh, delimiter=delimiter), None)
            return [c.strip() for c in row] if row else None
        except (OSError, UnicodeDecodeError, csv.Error):
            return None
    # parquet needs pandas; handled in _read_stats, columns derived there
    return None


def _read_stats(path: Path) -> dict[str, Any]:
    """Best-effort row count + per-column null counts (pandas optional)."""
    try:
        import pandas as pd
    except ImportError:
        return {}
    try:
        if path.suffix.lower() == ".parquet":
            df = pd.read_parquet(path)
        else:
            sep = "\t" if path.suffix.lower() == ".tsv" else ","
            df = pd.read_csv(path, sep=sep, nrows=_STATS_SAMPLE_ROWS)
    except Exception:
        return {}
    nulls = {str(col): int(df[col].isna().sum()) for col in df.columns}
    dtypes = {str(col): str(df[col].dtype) for col in df.columns}
    sampled = len(df) >= _STATS_SAMPLE_ROWS
    return {
        "n_rows_sampled": int(len(df)),
        "sampled": sampled,
        "nulls": nulls,
        "dtypes": dtypes,
        "columns": [str(c) for c in df.columns],
    }


def build_profile(path: str | Path) -> DatasetProfile | None:
    """Read a tabular file into a DatasetProfile; None if unreadable."""
    p = Path(path)
    if not p.exists() or not is_tabular(p):
        return None
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None

    stats = _read_stats(p)
    columns = _read_columns(p) or stats.get("columns")
    if not columns:
        return None
    dtypes = stats.get("dtypes", {})
    structure = {
        "n_cols": len(columns),
        "columns": [{"name": c, "dtype": dtypes.get(c, "unknown")} for c in columns],
    }
    statistics = {
        "n_rows_sampled": stats.get("n_rows_sampled"),
        "sampled": stats.get("sampled", False),
        "nulls": stats.get("nulls", {}),
    }
    return DatasetProfile(
        path=str(p.resolve()),
        column_fingerprint=column_fingerprint(columns),
        structure=structure,
        statistics=statistics,
        stats_mtime=mtime,
    )


def assess(profile: DatasetProfile, path: str | Path) -> Staleness:
    """Compare a stored profile against the current file state."""
    p = Path(path)
    if not p.exists():
        return "missing"
    columns = _read_columns(p) or _read_stats(p).get("columns")
    if not columns:
        return "missing"
    if column_fingerprint(columns) != profile.column_fingerprint:
        return "invalid"  # add/remove column → structure layer void
    try:
        if p.stat().st_mtime > profile.stats_mtime:
            return "stale"  # same schema, newer data → stats need recompute
    except OSError:
        return "missing"
    return "fresh"


class ProfileStore:
    """Disk-backed dataset_profile store keyed by resolved file path."""

    def __init__(self, store_dir: str | Path) -> None:
        self.dir = Path(store_dir)
        self._store = JsonlStore(self.dir / "profiles.jsonl")
        self._index: dict[str, DatasetProfile] = {}
        self._load()

    def _load(self) -> None:
        for row in self._store.read():
            try:
                profile = DatasetProfile.from_dict(row)
            except (KeyError, TypeError):  # domain decoding; JSON errors absorbed upstream
                continue
            self._index[_resolve(profile.path)] = profile

    def get(self, path: str | Path) -> DatasetProfile | None:
        return self._index.get(_resolve(path))

    def record(self, path: str | Path) -> DatasetProfile | None:
        """Generate/refresh a profile per the layered-staleness rule, persist it."""
        key = _resolve(path)
        existing = self._index.get(key)
        if existing is not None:
            status = assess(existing, path)
            if status == "fresh":
                existing.last_used_at = _utc_now()
                existing.stale = (
                    False  # a previously-staled profile that re-verifies is fresh again
                )
                self._index[key] = existing
                self._rewrite()
                return existing
            if status == "stale":
                fresh = build_profile(path)
                if fresh is not None:  # keep structure identity, refresh stats
                    fresh.created_at = existing.created_at
                    self._index[key] = fresh
                    self._rewrite()
                    return fresh
                existing.stale = True
                self._rewrite()
                return existing
            # invalid / missing → rebuild from scratch (fall through)
        built = build_profile(path)
        if built is None:
            return None
        self._index[key] = built
        self._rewrite()
        return built

    def all(self) -> list[DatasetProfile]:
        return list(self._index.values())

    def _rewrite(self) -> None:
        self._store.rewrite(prof.to_dict() for prof in self._index.values())


def _resolve(path: str | Path) -> str:
    with contextlib.suppress(OSError):
        return str(Path(path).resolve())
    return str(path)
