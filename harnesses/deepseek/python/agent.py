# -*- coding: utf-8 -*-
"""dsh (DeepSeek Harness) Python SDK 驱动的 DAA 演示入口。

未验证(需真实 key):本脚本依赖真实 DeepSeek API key 与联网模型端点,
在本任务环境中仅做了 SDK 签名核对(PyPI deepseek-harness-sdk 0.1.1rc1,
`from deepseek_harness import DeepSeekHarness`),未实际跑通模型调用。

用法:
    uv pip install deepseek-harness-sdk          # 装入仓库 .venv
    export DEEPSEEK_API_KEY=sk-...               # 必填
    export DSH_MODEL=deepseek-v4-flash           # 可选,默认 deepseek-v4-flash
    .venv/bin/python harnesses/deepseek/python/agent.py harnesses/deepseek/cordis.example.yml

cordis 配置参数指向 dsh 的 patch 覆盖层(含 mcp-daa 与 daa-capabilities 插件);
SDK 经 DSH_CORDIS_CONFIG 注入该配置。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_BIN = REPO_ROOT / ".venv" / "bin"


def main() -> int:
    try:
        from deepseek_harness import DeepSeekHarness
    except ImportError:
        print("缺少依赖:请先 `uv pip install deepseek-harness-sdk`")
        return 2

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("缺少 DEEPSEEK_API_KEY(未验证路径需真实 key)")
        return 2

    cordis = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent / "cordis.example.yml")
    if not cordis.is_file():
        print(f"cordis 配置不存在: {cordis}")
        return 2

    model = os.environ.get("DSH_MODEL", "deepseek-v4-flash")
    # 能力服务器经 PATH 查找(mcp-client 子进程继承本环境)。
    env = {"PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"}

    with DeepSeekHarness(
        provider="deepseek-official",
        model=model,
        cwd=str(REPO_ROOT),
        session_root=str(REPO_ROOT / "daa-capabilities-artifacts" / "dsh-sessions"),
        cordis=str(cordis),
        env=env,
    ) as harness:
        result = harness.run(
            "用 mcp__daa__tabular_read_file 读取 examples 下的一个小 CSV,"
            "再概括它的列结构。",
            session_id="daa-dsh-demo-1",
        )
    print(f"session_id={result.session_id} finish_reason={result.finish_reason}")
    print(result.final_response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
