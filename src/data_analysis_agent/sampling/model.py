"""v1 shim → ``data_analysis_agent.capabilities.sampling.model``(物理迁移)。"""

from __future__ import annotations

import sys as _sys

import data_analysis_agent.capabilities.sampling.model as _impl
from data_analysis_agent.capabilities.sampling.model import (  # noqa: F401
    ColumnSummary,
    TableSummary,
)

__all__ = ["ColumnSummary", "TableSummary"]

_sys.modules[__name__] = _impl
