"""v1 兼容 shim:实现已物理迁移至 ``data_analysis_agent.capabilities.sampling``。

本包只做 re-export,公共 API 与行为不变;勿在此添加实现。
"""

from __future__ import annotations

from data_analysis_agent.capabilities.sampling import (  # noqa: F401
    FIDELITY_LEVELS,
    ColumnSummary,
    ResultStore,
    RetrievedPage,
    SamplingConfig,
    TableSummary,
    compact_result,
    render_summary_dict,
    render_text_digest,
    summarize_text,
)

__all__ = [
    "FIDELITY_LEVELS",
    "ColumnSummary",
    "ResultStore",
    "RetrievedPage",
    "SamplingConfig",
    "TableSummary",
    "compact_result",
    "render_summary_dict",
    "render_text_digest",
    "summarize_text",
]
