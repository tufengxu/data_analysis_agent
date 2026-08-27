"""MCP stdio server(官方 ``mcp`` SDK 2.x 低层 API,动态 schema 完全来自 CapabilitySpec)。

用法:``data-agent-capabilities mcp``。每个能力一个 MCP tool;结果以 JSON envelope
(ok/content/data/...) 作为文本内容返回,is_error 对应 envelope["ok"],保证三传输
(进程内 / MCP / CLI)输出等价,便于适配层与一致性测试消费。
"""

from __future__ import annotations

import json
from typing import Any

from data_analysis_agent.capabilities.contracts import (
    CapabilityRegistry,
    Permission,
)

_SERVER_NAME = "data-analysis-agent-capabilities"


def _tool_annotations(permission: Permission) -> dict[str, Any]:
    return {
        "readOnlyHint": permission == Permission.READ_ONLY,
        "destructiveHint": permission == Permission.EXECUTES_CODE,
    }


def build_mcp_server(registry: CapabilityRegistry) -> Any:
    """构建已注册 handlers 的低层 ``mcp.server.Server``(延迟 import mcp)。"""

    from mcp.server import Server
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
        TextContent,
        Tool,
        ToolAnnotations,
    )

    server: Any = Server(_SERVER_NAME)

    async def handle_list_tools(ctx: Any, params: PaginatedRequestParams) -> ListToolsResult:
        _ = (ctx, params)
        return ListToolsResult(
            tools=[
                Tool(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema,
                    annotations=ToolAnnotations(**_tool_annotations(spec.permission)),
                )
                for spec in registry.specs()
            ]
        )

    async def handle_call_tool(ctx: Any, params: CallToolRequestParams) -> CallToolResult:
        # 能力注册表的 fail-closed 分发(纯内存路由,无数据库/查询语言参与)。
        _ = ctx
        capability_name = str(params.name)
        capability_input = dict(params.arguments or {})
        envelope = await registry.dispatch(capability_name, capability_input)
        payload_text = json.dumps(envelope, ensure_ascii=False, default=str)
        return CallToolResult(
            content=[TextContent(type="text", text=payload_text)],
            is_error=not bool(envelope["ok"]),
        )

    server.add_request_handler("tools/list", PaginatedRequestParams, handle_list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)
    return server


def run_stdio(registry: CapabilityRegistry | None = None) -> None:
    """阻塞运行 stdio server(入口:CLI ``mcp`` 子命令)。"""

    import anyio
    from mcp.server.stdio import stdio_server

    if registry is None:
        from .registry import build_registry

        registry = build_registry()
    server = build_mcp_server(registry)

    async def _serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
                raise_exceptions=False,
            )

    anyio.run(_serve)
