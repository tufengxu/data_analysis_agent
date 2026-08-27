#!/usr/bin/env python3
"""真实 stdio 框架层冒烟:经 mcp SDK 的 stdio_client 拉起 `data-agent-capabilities mcp`
子进程,做 initialize → tools/list → tools/call,断言 envelope 正常。

与 pytest 内的内存流传输一致性测试互补:这里验证 console script + stdio 分帧。
用法:.venv/bin/python examples/v2/smoke_stdio_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
BIN = REPO / ".venv" / "bin" / "data-agent-capabilities"


async def main() -> int:
    from mcp import StdioServerParameters
    from mcp.client.session import ClientSession
    from mcp.client.stdio import stdio_client

    tmp = Path(tempfile.mkdtemp(prefix="daa-stdio-smoke-"))
    (tmp / "mini.csv").write_text("a,b\n1,x\n2,y\n3,z\n", encoding="utf-8")
    env = {
        **os.environ,
        "DAA_CAPABILITIES_HOME": str(tmp / "store"),
        "DAA_CAPABILITIES_ARTIFACTS": str(tmp / "artifacts"),
        "DAA_CAPABILITIES_ALLOWED_ROOTS": str(tmp),
        "DAA_CAPABILITIES_EVOLUTION_ROOT": str(tmp / "traj"),
    }
    params = StdioServerParameters(command=str(BIN), args=["mcp"], env=env)

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(
                "tabular_read_file", {"file_path": str(tmp / "mini.csv")}
            )

    server_name = init.server_info.name
    envelope = json.loads(result.content[0].text) if result.content else {}
    print(f"server: {server_name}")
    print(f"tools: {len(tools.tools)}")
    print(f"call ok: {envelope.get('ok')} | head: {str(envelope.get('content', ''))[:40]!r}")

    ok = (
        server_name == "data-analysis-agent-capabilities"
        and len(tools.tools) >= 19
        and envelope.get("ok") is True
        and "a,b" in str(envelope.get("content", ""))
    )
    print("STDIO SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
