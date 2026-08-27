"""统一暴露层:CapabilityRegistry 全量装配 → MCP stdio server + CLI。

每个能力经 ``data-agent-capabilities mcp``(官方 ``mcp`` SDK,stdio)与
``data-agent-capabilities <子命令>`` 两种传输调用;两基座适配层默认共用同一条
MCP stdio 通道。传输一致性(进程内直调 vs MCP vs CLI 等价输出)由测试守护。
"""
