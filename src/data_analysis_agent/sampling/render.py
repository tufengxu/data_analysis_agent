"""v1 shim → ``data_analysis_agent.capabilities.sampling.render``(物理迁移)。"""

from __future__ import annotations

import sys as _sys

import data_analysis_agent.capabilities.sampling.render as _impl
from data_analysis_agent.capabilities.sampling.render import (  # noqa: F401
    render_summary_dict,
    render_text_digest,
)

__all__ = ["render_summary_dict", "render_text_digest"]

_sys.modules[__name__] = _impl
