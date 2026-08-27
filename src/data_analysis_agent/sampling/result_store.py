"""v1 shim → ``data_analysis_agent.capabilities.sampling.result_store``(物理迁移)。"""

from __future__ import annotations

import sys as _sys

import data_analysis_agent.capabilities.sampling.result_store as _impl
from data_analysis_agent.capabilities.sampling.result_store import (  # noqa: F401
    ResultStore,
    RetrievedPage,
)

__all__ = ["ResultStore", "RetrievedPage"]

_sys.modules[__name__] = _impl
