"""Domain memory data model.

Two carriers, by deliberate design (decision: 记结构不记数值):
* MemoryEntry — textual, stable domain knowledge: metric definitions,
  analysis preferences, open data concerns. Never a numeric finding.
* DatasetProfile — a dataset's *structure layer* (schema, stable) plus a
  *statistics layer* (distributions, volatile). Split so the structure can
  survive a data update while the stats go stale (decision: 分层失效).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

MemoryKind = Literal["metric_definition", "analysis_pref", "open_concern"]

# A metric is treated as confirmed once the model has leaned on it this many
# times without the user correcting or deleting it (the light-confirm loop —
# human cost amortized to zero rather than an explicit prompt).
CONFIRM_AFTER_USES = 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def column_fingerprint(columns: list[str]) -> str:
    """Stable hash of the sorted column-name list — a table's structural identity.

    Order-independent (sorted): reordering columns is not a structural change.
    Duplicates are kept (not de-duped to a set): a pathological CSV with two
    same-named columns is a genuinely different shape. A fingerprint match means
    the same schema shape; a mismatch (added/removed column) invalidates the
    whole profile.
    """
    joined = "\n".join(sorted(columns))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


@dataclass
class MemoryEntry:
    """One piece of stable, textual domain knowledge."""

    kind: MemoryKind
    key: str
    content: str
    source_session: str = ""
    created_at: str = field(default_factory=_utc_now)
    last_used_at: str = field(default_factory=_utc_now)
    use_count: int = 0
    confirmed: bool = True  # default for non-metric kinds; remember_metric() sets metrics to False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MemoryEntry:
        fields = {
            "kind",
            "key",
            "content",
            "source_session",
            "created_at",
            "last_used_at",
            "use_count",
            "confirmed",
        }
        kwargs = {k: v for k, v in d.items() if k in fields}
        # A metric with no explicit `confirmed` must default to UNCONFIRMED
        # (ADR 0004 light-confirm), not the dataclass `True` — otherwise legacy
        # rows missing the field would silently bypass confirmation.
        if kwargs.get("kind") == "metric_definition" and "confirmed" not in d:
            kwargs["confirmed"] = False
        return cls(**kwargs)


@dataclass
class DatasetProfile:
    """Structure layer (stable) + statistics layer (volatile) for one file."""

    path: str
    column_fingerprint: str
    structure: dict[str, Any]  # {columns: [{name, dtype}], n_cols}
    statistics: dict[str, Any]  # {n_rows, nulls: {col: count}}
    stats_mtime: float
    stale: bool = False
    created_at: str = field(default_factory=_utc_now)
    last_used_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DatasetProfile:
        fields = {
            "path",
            "column_fingerprint",
            "structure",
            "statistics",
            "stats_mtime",
            "stale",
            "created_at",
            "last_used_at",
        }
        return cls(**{k: v for k, v in d.items() if k in fields})

    @property
    def columns(self) -> list[str]:
        cols = self.structure.get("columns", [])
        return [c.get("name", "") for c in cols if isinstance(c, dict)]
