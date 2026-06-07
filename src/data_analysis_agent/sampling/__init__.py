"""Sampling-based compaction for large tool results.

Two seams share one structured model and one renderer:
    * high fidelity — :mod:`sandbox_summary` runs inside python_exec on the real
      DataFrame (exact stats);
    * universal fallback — :mod:`text_summary` runs in the harness on any
      oversized string (pure stdlib, sample-estimated stats).

See ``docs/superpowers/specs/2026-06-06-data-sampling-compaction-design.md``.
"""

from __future__ import annotations

from .config import FIDELITY_LEVELS, SamplingConfig
from .model import ColumnSummary, TableSummary
from .render import render_summary_dict, render_text_digest
from .text_summary import compact_result, summarize_text

__all__ = [
    "FIDELITY_LEVELS",
    "ColumnSummary",
    "SamplingConfig",
    "TableSummary",
    "compact_result",
    "render_summary_dict",
    "render_text_digest",
    "summarize_text",
]
