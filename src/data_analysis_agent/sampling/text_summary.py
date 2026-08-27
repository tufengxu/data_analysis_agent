"""v1 shim → ``data_analysis_agent.capabilities.sampling.text_summary``(物理迁移)。

``sys.modules`` 别名使 ``sampling.text_summary._private`` 等私有访问(测试用)也命中
真实模块;显式 import 供 mypy 解析。
"""

from __future__ import annotations

import sys as _sys

import data_analysis_agent.capabilities.sampling.text_summary as _impl
from data_analysis_agent.capabilities.sampling.text_summary import (  # noqa: F401
    compact_result,
    summarize_text,
)

__all__ = ["compact_result", "summarize_text"]

_sys.modules[__name__] = _impl
