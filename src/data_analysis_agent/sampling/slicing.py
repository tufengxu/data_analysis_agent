"""v1 shim → ``data_analysis_agent.capabilities.sampling.slicing``(D6 查询下取)。

``sys.modules`` 别名使 ``sampling.slicing._private`` 等私有访问(测试用)也命中
真实模块;显式 import 供 mypy 解析。
"""

from __future__ import annotations

import sys as _sys

import data_analysis_agent.capabilities.sampling.slicing as _impl
from data_analysis_agent.capabilities.sampling.slicing import (  # noqa: F401
    SliceError,
    TableSlice,
    parse_filter,
    render_slice,
    slice_stored_table,
)

__all__ = [
    "SliceError",
    "TableSlice",
    "parse_filter",
    "render_slice",
    "slice_stored_table",
]

_sys.modules[__name__] = _impl
