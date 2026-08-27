"""v1 shim → ``data_analysis_agent.capabilities.sampling.config``(物理迁移)。"""

from __future__ import annotations

import sys as _sys

import data_analysis_agent.capabilities.sampling.config as _impl
from data_analysis_agent.capabilities.sampling.config import (  # noqa: F401
    FIDELITY_LEVELS,
    SamplingConfig,
)

__all__ = ["FIDELITY_LEVELS", "SamplingConfig"]

_sys.modules[__name__] = _impl
