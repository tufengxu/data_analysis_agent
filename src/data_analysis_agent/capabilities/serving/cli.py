"""``data-agent-capabilities`` CLI:能力层的兜底/调试传输(与 MCP 同一注册表)。

子命令:
    mcp                       启动 MCP stdio server(需 ``serving`` extra)
    list                      列出全部能力(name/domain/permission/描述)
    call NAME [--input JSON]  调用一个能力,打印 JSON envelope(失败 exit 1)
    compact [stdin]           采样压缩捷径(--fidelity/--max-chars/--pressure)
    retrieve RESULT_ID        分页回取原文(--offset/--limit/--query)
"""

from __future__ import annotations

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import typer

from data_analysis_agent.capabilities.contracts import CapabilityRegistry

app = typer.Typer(
    name="data-agent-capabilities",
    help="DataAnalysisAgent v2 能力层统一 CLI(MCP 同源,兜底/调试通道)",
    no_args_is_help=True,
    add_completion=False,
)


def _run_coro(coro: Any) -> Any:
    """同步执行协程;若已在事件循环内(测试/异步宿主嵌入场景)则开工作线程跑新循环。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _registry() -> CapabilityRegistry:
    from .registry import build_registry

    return build_registry()


def _dump(envelope: dict[str, Any]) -> None:
    print(json.dumps(envelope, ensure_ascii=False, default=str))


@app.command("mcp")
def mcp_command() -> None:
    """启动 MCP stdio server(官方 mcp SDK)。"""
    try:
        from .mcp_server import run_stdio
    except ImportError as exc:  # pragma: no cover - depends on extra
        print(f"mcp SDK 未安装: {exc}(pip install -e '.[serving]')", file=sys.stderr)
        raise typer.Exit(code=2) from exc
    run_stdio()


@app.command("list")
def list_command() -> None:
    """列出全部能力契约。"""
    registry = _registry()
    _dump({"ok": True, "capabilities": [spec.to_public_dict() for spec in registry.specs()]})


@app.command("call")
def call_command(
    name: str = typer.Argument(..., help="能力名,如 tabular_read_file"),
    input_json: str = typer.Option(  # noqa: B008 — typer 声明式参数惯例
        None, "--input", "-i", help='JSON 对象,如 \'{"file_path": "a.csv"}\''
    ),
    input_file: Path = typer.Option(None, "--input-file", help="从 JSON 文件读输入"),  # noqa: B008
) -> None:
    """调用一个能力并打印 JSON envelope(非 ok 时 exit 1)。"""
    if input_json is not None and input_file is not None:
        print("--input 与 --input-file 二选一", file=sys.stderr)
        raise typer.Exit(code=2)
    try:
        if input_file is not None:
            input_data = json.loads(input_file.read_text(encoding="utf-8"))
        elif input_json is not None:
            input_data = json.loads(input_json)
        else:
            input_data = {}
    except json.JSONDecodeError as exc:
        print(f"输入不是合法 JSON: {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from exc
    if not isinstance(input_data, dict):
        print("输入必须是 JSON 对象", file=sys.stderr)
        raise typer.Exit(code=2)
    envelope = _run_coro(_registry().dispatch(name, input_data))
    _dump(envelope)
    if not envelope["ok"]:
        raise typer.Exit(code=1)


@app.command("compact")
def compact_command(
    max_chars: int = typer.Option(50_000, "--max-chars"),
    pressure: float = typer.Option(0.0, "--pressure", help="上下文压力 0..1"),
    fidelity: str = typer.Option(None, "--fidelity", help="low|mid|high"),
    result_id: str = typer.Option(None, "--result-id"),
    tool_name: str = typer.Option("", "--tool-name"),
) -> None:
    """读 stdin 原文,输出压缩 envelope(含召回句柄)。"""
    content = sys.stdin.read()
    input_data: dict[str, Any] = {
        "content": content,
        "max_chars": max_chars,
        "context_pressure": pressure,
        "tool_name": tool_name,
    }
    if fidelity:
        input_data["fidelity_level"] = fidelity
    if result_id:
        input_data["result_id"] = result_id
    envelope = _run_coro(_registry().dispatch("sampling_compact_result", input_data))
    _dump(envelope)
    if not envelope["ok"]:
        raise typer.Exit(code=1)


@app.command("retrieve")
def retrieve_command(
    result_id: str = typer.Argument(...),
    offset: int = typer.Option(0),
    limit: int = typer.Option(50),
    query: str = typer.Option(None),
) -> None:
    """按行分页回取被压缩前的原始结果。"""
    input_data: dict[str, Any] = {"result_id": result_id, "offset": offset, "limit": limit}
    if query:
        input_data["query"] = query
    envelope = _run_coro(_registry().dispatch("retrieve_result", input_data))
    _dump(envelope)
    if not envelope["ok"]:
        raise typer.Exit(code=1)


def main() -> None:  # console script 入口
    app()


if __name__ == "__main__":
    main()
