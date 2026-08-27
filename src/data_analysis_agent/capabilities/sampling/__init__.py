"""Sampling-based compaction for large tool results (v2 capability home).

Two seams share one structured model and one renderer:
    * high fidelity — :mod:`sandbox_summary` runs inside python_exec on the real
      DataFrame (exact stats);
    * universal fallback — :mod:`text_summary` runs in the harness on any
      oversized string (pure stdlib, sample-estimated stats).

Physically migrated from ``data_analysis_agent.sampling`` (v1 path is now a
pure re-export shim; public API and behavior unchanged). The
``ToolResultCompactor`` seam contract lives in :mod:`compactor`.
"""

from __future__ import annotations

from .compactor import (
    CompactRequest,
    CompactResult,
    DefaultToolResultCompactor,
    ToolResultCompactor,
    recall_hint,
)
from .config import FIDELITY_LEVELS, SamplingConfig
from .model import ColumnSummary, TableSummary
from .render import render_summary_dict, render_text_digest
from .result_store import ResultStore, RetrievedPage
from .text_summary import compact_result, summarize_text

__all__ = [
    "CompactRequest",
    "CompactResult",
    "DefaultToolResultCompactor",
    "FIDELITY_LEVELS",
    "ColumnSummary",
    "ResultStore",
    "RetrievedPage",
    "SamplingConfig",
    "TableSummary",
    "ToolResultCompactor",
    "compact_result",
    "recall_hint",
    "render_summary_dict",
    "render_text_digest",
    "summarize_text",
]
