"""CLI entry point for the data analysis agent."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel

from .config import AgentConfig
from .events import (
    CompleteEvent,
    ErrorEvent,
    RequestStartEvent,
    StateChangeEvent,
    StreamTextEvent,
    ToolResultEvent,
    ToolUseEvent,
)

# Assembly lives in the composition root; re-exported here so existing callers
# (and tests) importing them from __main__ keep working.
from .runtime import (  # noqa: F401
    AgentRuntime,
    build_message_store,
    build_permission_engine,
    build_registry,
    build_skill_registry,
)
from .telemetry import parse_explicit_feedback

console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("data_analysis_agent")


class _ShutdownManager:
    """Manages graceful shutdown on SIGINT / SIGTERM."""

    def __init__(self) -> None:
        self._shutdown_event = asyncio.Event()
        self._original_sigint: Any = None
        self._original_sigterm: Any = None

    def install(self) -> None:
        """Install signal handlers (must be called inside a running loop)."""
        try:
            asyncio.get_running_loop()
            self._original_sigint = signal.signal(signal.SIGINT, self._handle_signal)
            self._original_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
        except RuntimeError:
            pass  # No running event loop yet

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        logger.info("Received signal %s, initiating graceful shutdown...", signum)
        self._shutdown_event.set()

    def is_shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()

    def restore(self) -> None:
        """Restore original signal handlers."""
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)


_shutdown_manager = _ShutdownManager()


class ConsoleApprovalHandler:
    """Interactive y/N gate for ASK permission decisions.

    Pauses the active rich Live display (if any) so the prompt is readable,
    then resumes it.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.live: Live | None = None

    async def __call__(self, tool_name: str, tool_input: dict[str, Any]) -> bool:
        live = self.live
        if live is not None:
            live.stop()
        try:
            params = json.dumps(tool_input, ensure_ascii=False)
            if len(params) > 200:
                params = params[:200] + "…"
            answer = await asyncio.to_thread(
                self.console.input,
                f"[yellow]允许执行 {tool_name} {params} ? \\[y/N]: [/yellow]",
            )
            return answer.strip().lower() in ("y", "yes")
        finally:
            if live is not None:
                live.start()


def build_runtime(
    config: AgentConfig,
    persist_path: str | Path | None,
    approval_handler: ConsoleApprovalHandler | None = None,
) -> AgentRuntime:
    """Assemble the runtime via the composition root, then surface resume info."""
    runtime = AgentRuntime.from_config(
        config, persist_path=persist_path, approval_handler=approval_handler
    )
    if persist_path and len(runtime.session.history) > 0:
        console.print(f"[dim]已恢复会话：{len(runtime.session.history)} 条历史消息[/dim]")
    return runtime


async def run_turn(
    runtime: AgentRuntime,
    query: str,
    shutdown: _ShutdownManager | None = None,
    approval: ConsoleApprovalHandler | None = None,
) -> None:
    """Run one conversation turn and stream output to the terminal."""
    accumulated_text = ""
    current_tool = ""
    artifacts: list[str] = []

    # Held explicitly so an early break (shutdown/interrupt) still closes the
    # generator — that's what triggers ledger closure and history write-back.
    stream = runtime.session.send(query)

    with Live(console=console, refresh_per_second=10) as live:
        if approval is not None:
            approval.live = live
        try:
            async for event in stream:
                if shutdown and shutdown.is_shutdown_requested():
                    logger.info("Shutdown requested, stopping agent loop.")
                    break
                if isinstance(event, StreamTextEvent):
                    accumulated_text += event.text
                    live.update(Panel(Markdown(accumulated_text), title="Agent"))

                elif isinstance(event, ToolUseEvent):
                    current_tool = event.tool_name
                    live.update(
                        Panel(
                            f"Using tool: **{event.tool_name}**\n"
                            f"```json\n{json.dumps(event.parameters, indent=2)}\n```",
                            title="Tool Call",
                            border_style="blue",
                        )
                    )

                elif isinstance(event, ToolResultEvent):
                    artifacts.extend(event.artifacts)
                    display = event.content[:2000] + ("..." if len(event.content) > 2000 else "")
                    live.update(
                        Panel(
                            display,
                            title=f"Tool Result: {current_tool}",
                            border_style="green" if not event.is_error else "red",
                        )
                    )

                elif isinstance(event, RequestStartEvent):
                    live.update(
                        Panel(
                            f"Model: {event.model_id} | Turn: {event.turn_count}",
                            title="Thinking...",
                            border_style="yellow",
                        )
                    )

                elif isinstance(event, StateChangeEvent):
                    if event.new_state == "AWAITING_CONFIRMATION":
                        live.update(
                            Panel(
                                f"等待确认: {event.reason}",
                                title="Permission",
                                border_style="yellow",
                            )
                        )

                elif isinstance(event, ErrorEvent):
                    live.update(
                        Panel(
                            f"Error: {event.error}",
                            title="Error",
                            border_style="red",
                        )
                    )

                elif isinstance(event, CompleteEvent):
                    live.update(
                        Panel(
                            Markdown(accumulated_text or event.final_text),
                            title=f"Complete ({event.terminal_reason})",
                            border_style="green",
                        )
                    )
        finally:
            await stream.aclose()
            if approval is not None:
                approval.live = None

    if artifacts:
        console.print("[bold green]生成产物:[/bold green]")
        for path in artifacts:
            console.print(f"  📊 {path}")


async def run_single(
    query: str,
    config: AgentConfig,
    persist_path: str | Path | None,
) -> None:
    """One-shot mode: a single query in a fresh (or resumed) session."""
    _shutdown_manager.install()
    approval = ConsoleApprovalHandler(console)
    runtime = build_runtime(config, persist_path, approval_handler=approval)
    try:
        await run_turn(runtime, query, _shutdown_manager, approval)
    finally:
        await runtime.shutdown()


async def run_interactive(
    config: AgentConfig,
    persist_path: str | Path | None,
) -> None:
    """Interactive mode: one session, one kernel, one event loop for all turns."""
    _shutdown_manager.install()
    console.print(
        Panel(
            "[bold blue]Data Analysis Agent[/bold blue]\n"
            f"Model: {config.model}\n"
            "Type 'exit' or 'quit' to leave.",
            title="Welcome",
        )
    )
    approval = ConsoleApprovalHandler(console)
    runtime = build_runtime(config, persist_path, approval_handler=approval)
    try:
        while not _shutdown_manager.is_shutdown_requested():
            try:
                query = await asyncio.to_thread(console.input, "[bold green]> [/bold green]")
            except (EOFError, KeyboardInterrupt):
                break
            query = query.strip()
            if query.lower() in ("exit", "quit", "q"):
                break
            if not query:
                continue
            feedback = parse_explicit_feedback(query)
            if feedback is not None:
                ok = runtime.session.attach_feedback(feedback)
                console.print(
                    "[dim]已记录反馈,谢谢。[/dim]" if ok else "[dim]当前无可反馈的轮次。[/dim]"
                )
                continue
            await run_turn(runtime, query, _shutdown_manager, approval)
            console.print()
    finally:
        await runtime.shutdown()


def main() -> None:
    """Main CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Data Analysis Agent")
    parser.add_argument("query", nargs="?", help="Natural language query")
    parser.add_argument("--config", "-c", help="Path to config file")
    parser.add_argument("--model", "-m", help="Model ID")
    parser.add_argument("--max-turns", type=int, help="Maximum turns")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--persist", "-p", help="Path to JSONL message store")
    args = parser.parse_args()

    config = AgentConfig.from_file(args.config) if args.config else AgentConfig.from_env()

    if args.model:
        config.model = args.model
    if args.max_turns:
        config.max_turns = args.max_turns

    if not config.api_key:
        console.print(
            "[red]Error: ANTHROPIC_API_KEY not set.[/red]\n"
            "Set it via environment variable or config file."
        )
        sys.exit(1)

    try:
        if args.interactive or not args.query:
            asyncio.run(run_interactive(config, args.persist))
        else:
            asyncio.run(run_single(args.query, config, args.persist))
    finally:
        _shutdown_manager.restore()


if __name__ == "__main__":
    main()
