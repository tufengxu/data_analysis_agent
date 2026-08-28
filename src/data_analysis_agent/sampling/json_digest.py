"""v1 shim → ``data_analysis_agent.capabilities.sampling.json_digest``(D7)。

``sys.modules`` 别名使私有访问(测试用)也命中真实模块;显式 import 供 mypy 解析。
"""

from __future__ import annotations

import sys as _sys

import data_analysis_agent.capabilities.sampling.json_digest as _impl
from data_analysis_agent.capabilities.sampling.json_digest import (  # noqa: F401
    build_json_digest,
    parse_json_payload,
)

__all__ = ["build_json_digest", "parse_json_payload"]

_sys.modules[__name__] = _impl
