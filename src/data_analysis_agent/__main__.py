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

from .agent_loop import AgentLoop, AgentLoopConfig
from .config import AgentConfig
from .context.compression import ContextCompressor
from .events import (
    CompleteEvent,
    ErrorEvent,
    RequestStartEvent,
    StreamTextEvent,
    ToolResultEvent,
    ToolUseEvent,
)
from .persistence import MessageStore
from .security.permissions import (
    PermissionBehavior,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from .skills.builtin import (
    CorrelationAnalysisSkill,
    DescriptiveAnalysisSkill,
    TrendAnalysisSkill,
)
from .skills.registry import SkillRegistry
from .tools import FileReadTool, NlQueryTool, PythonAnalysisTool, ToolRegistry, VisualizationTool

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
        """Install signal handlers."""
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


def build_registry(config: AgentConfig | None = None) -> ToolRegistry:
    """Build and configure the tool registry with built-in tools."""
    registry = ToolRegistry()
    sampling_config = config.sampling_config() if config else None
    registry.register(FileReadTool())
    registry.register(PythonAnalysisTool(sampling_config=sampling_config))
    registry.register(NlQueryTool())
    registry.register(VisualizationTool())

    if config:
        for pattern in config.deny_patterns:
            registry.add_deny_pattern(pattern)
        if config.permission_mode == "plan":
            registry.add_deny_pattern("python_analysis")
            registry.add_deny_pattern("visualization")

    return registry


def build_skill_registry() -> SkillRegistry:
    """Build and configure the skill registry."""
    skills = SkillRegistry()
    skills.register(DescriptiveAnalysisSkill())
    skills.register(CorrelationAnalysisSkill())
    skills.register(TrendAnalysisSkill())
    return skills


def build_message_store(persist_path: str | Path | None) -> MessageStore | None:
    """Build a message store when persistence is requested."""
    return MessageStore(persist_path) if persist_path else None


def build_permission_engine(config: AgentConfig) -> PermissionEngine | None:
    """Build permission rules from runtime configuration.

    Default mode without deny rules preserves the existing non-interactive CLI
    behavior. Once permission config is present, the engine is fail-closed for
    explicit asks and deny-first for matching deny patterns.
    """
    mode_map = {
        "default": PermissionMode.DEFAULT,
        "plan": PermissionMode.PLAN,
        "auto": PermissionMode.AUTO,
        "bypass": PermissionMode.BYPASS,
    }
    mode = mode_map.get(config.permission_mode, PermissionMode.DEFAULT)

    if mode == PermissionMode.DEFAULT and not config.deny_patterns:
        return None

    engine = PermissionEngine(mode=mode)
    for pattern in config.deny_patterns:
        engine.add_rule(PermissionRule(pattern, PermissionBehavior.DENY))

    if mode == PermissionMode.PLAN:
        engine.add_rule(PermissionRule("python_analysis", PermissionBehavior.DENY))
        engine.add_rule(PermissionRule("visualization", PermissionBehavior.DENY))
        engine.add_rule(PermissionRule("read_file", PermissionBehavior.ALLOW))
        engine.add_rule(PermissionRule("nl_query", PermissionBehavior.ALLOW))
    elif mode in (PermissionMode.DEFAULT, PermissionMode.AUTO):
        engine.add_rule(PermissionRule("*", PermissionBehavior.ALLOW))

    return engine


async def run_agent(
    query: str,
    config: AgentConfig,
    shutdown: _ShutdownManager | None = None,
    persist_path: str | Path | None = None,
) -> None:
    """Run a single agent query and stream output."""
    loop_config = AgentLoopConfig(
        system_prompt=config.system_prompt,
        max_turns=config.max_turns,
        max_tokens=config.max_tokens,
        model=config.model,
        api_key=config.api_key,
    )
    registry = build_registry(config)
    skill_registry = build_skill_registry()
    compressor = ContextCompressor(
        budget_tokens=config.context_budget_tokens,
        enable_snip=True,
        enable_collapse=True,
    )
    store = build_message_store(persist_path)
    permission_engine = build_permission_engine(config)

    agent = AgentLoop(
        loop_config,
        registry,
        compressor=compressor,
        store=store,
        skill_registry=skill_registry,
        permission_engine=permission_engine,
        sampling_config=config.sampling_config(),
    )

    accumulated_text = ""
    current_tool = ""

    with Live(console=console, refresh_per_second=10) as live:
        async for event in agent.run(query):
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
                        f"Using tool: **{event.tool_name}**\n```json\n{json.dumps(event.parameters, indent=2)}\n```",
                        title="Tool Call",
                        border_style="blue",
                    )
                )

            elif isinstance(event, ToolResultEvent):
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

    _shutdown_manager.install()
    try:
        if args.interactive or not args.query:
            # Interactive mode
            console.print(
                Panel(
                    "[bold blue]Data Analysis Agent[/bold blue]\n"
                    f"Model: {config.model}\n"
                    "Type 'exit' or 'quit' to leave.",
                    title="Welcome",
                )
            )
            while True:
                try:
                    query = console.input("[bold green]> [/bold green]")
                except (EOFError, KeyboardInterrupt):
                    break
                query = query.strip()
                if query.lower() in ("exit", "quit", "q"):
                    break
                if not query:
                    continue
                asyncio.run(run_agent(query, config, _shutdown_manager, args.persist))
                console.print()
        else:
            asyncio.run(run_agent(args.query, config, _shutdown_manager, args.persist))
    finally:
        _shutdown_manager.restore()


if __name__ == "__main__":
    main()
