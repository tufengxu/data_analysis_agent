"""Structured carriers for the L0/L1/L2 summary layers.

The same ``to_dict()`` shape is produced by both the in-harness text path
(:mod:`text_summary`) and the sandbox path (:mod:`sandbox_summary`, which emits
a plain dict because it cannot import this package). The single renderer in
:mod:`render` consumes that shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ColumnSummary:
    """Per-column summary (L1).

    ``stats`` is kind-specific:
        - numeric: ``min`` / ``max`` / ``mean`` / ``std`` /
          ``quantiles`` (list of ``[prob, value]``) / ``n_outliers``
        - categorical / bool: ``cardinality`` /
          ``top_k`` (list of ``[value, count]``) / ``tail_truncated``
        - datetime: ``min`` / ``max``
    """

    name: str
    kind: str
    count: int
    null_count: int
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "count": self.count,
            "null_count": self.null_count,
            "stats": self.stats,
        }


@dataclass
class TableSummary:
    """Full table summary: metadata (L0) + columns (L1) + sample rows (L2)."""

    n_rows: int
    n_cols: int
    columns: list[ColumnSummary] = field(default_factory=list)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    outlier_rows: list[dict[str, Any]] = field(default_factory=list)
    sampling_method: str = ""
    fidelity_level: str = "mid"
    notes: list[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "columns": [c.to_dict() for c in self.columns],
            "sample_rows": self.sample_rows,
            "outlier_rows": self.outlier_rows,
            "sampling_method": self.sampling_method,
            "fidelity_level": self.fidelity_level,
            "notes": self.notes,
            "truncated": self.truncated,
        }
