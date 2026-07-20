"""CLI entry point for the data analysis agent."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from collections.abc import Sequence
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
    UsageEvent,
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
from .workspace import Project, RunManifest

console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("data_analysis_agent")


def parse_memory_command(text: str) -> tuple[str, str, str] | None:
    """Parse a /define or /pref capture command; None if it is not one.

    /define <name>=<definition> -> ("metric_definition", name, definition)
    /pref <preference text>     -> ("analysis_pref", <derived key>, text)
    Malformed commands return None so the caller can print usage.
    """
    s = text.strip()
    if s == "/define" or s.startswith("/define "):
        name, sep, definition = s[len("/define") :].strip().partition("=")
        name, definition = name.strip(), definition.strip()
        if sep and name and definition:
            return ("metric_definition", name, definition)
        return None
    if s == "/pref" or s.startswith("/pref "):
        body = s[len("/pref") :].strip()
        return ("analysis_pref", body[:32], body) if body else None
    return None


def apply_memory_command(injector: Any, parsed: tuple[str, str, str]) -> str:
    """Write a parsed memory command via the injector; return a user message."""
    kind, key, content = parsed
    if kind == "metric_definition":
        # Explicit user definition: trusted immediately (confirmed).
        injector.remember_metric(key, content, confirmed=True)
        return f"已记录口径定义:{key} = {content}"
    injector.remember_pref(content, key=key)
    return f"已记录分析偏好:{content}"


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
    analysis_paths: Sequence[str | Path] | None = None,
    project: Project | None = None,
) -> AgentRuntime:
    """Assemble the runtime via the composition root, then surface resume info."""
    runtime = AgentRuntime.from_config(
        config,
        persist_path=persist_path,
        approval_handler=approval_handler,
        analysis_paths=analysis_paths,
        project=project,
    )
    # Resume only fires under an explicit --persist (a project allocates a fresh
    # run id per invocation, so there is nothing to resume within a project run).
    if persist_path and not project and len(runtime.session.history) > 0:
        console.print(f"[dim]已恢复会话：{len(runtime.session.history)} 条历史消息[/dim]")
    return runtime


def _now_iso() -> str:
    """UTC ISO-8601 timestamp for run manifests."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _merge_run_stats(agg: dict[str, Any], stats: dict[str, Any]) -> None:
    """Fold one turn's stats into a session accumulator (interactive mode)."""
    for key, value in stats["event_counts"].items():
        agg["event_counts"][key] = agg["event_counts"].get(key, 0) + value
    for key, value in stats["tool_calls"].items():
        agg["tool_calls"][key] = agg["tool_calls"].get(key, 0) + value
    for artifact in stats["artifacts"]:
        if artifact not in agg["artifacts"]:
            agg["artifacts"].append(artifact)
    if stats["terminal_reason"]:
        agg["terminal_reason"] = stats["terminal_reason"]
    agg["token_usage"]["input_tokens"] += stats["token_usage"]["input_tokens"]
    agg["token_usage"]["output_tokens"] += stats["token_usage"]["output_tokens"]


def _record_run(
    runtime: AgentRuntime,
    request: str,
    authorized_paths: Sequence[str | Path] | None,
    stats: dict[str, Any],
    started_at: str,
    finished_at: str,
) -> Path | None:
    """Persist a RunManifest when the run is inside a project; else no-op."""
    if runtime.project is None or runtime.run_id is None:
        return None
    usage = stats["token_usage"]
    token_usage = usage if (usage["input_tokens"] or usage["output_tokens"]) else None
    # Sensitive run: never write the raw user query into the manifest.
    request_text = "<redacted: sensitive-mode>" if runtime.sensitive_mode else request
    run = RunManifest(
        run_id=runtime.run_id,
        project_id=runtime.project.project_id,
        started_at=started_at,
        finished_at=finished_at,
        request=request_text,
        authorized_paths=[str(p) for p in (authorized_paths or [])],
        session_id=runtime.session.meta.session_id,
        event_counts=stats["event_counts"],
        tool_calls=stats["tool_calls"],
        artifacts=stats["artifacts"],
        terminal_reason=stats["terminal_reason"],
        token_usage=token_usage,
        warnings=[],
    )
    return runtime.project.add_run(run)


async def run_turn(
    runtime: AgentRuntime,
    query: str,
    shutdown: _ShutdownManager | None = None,
    approval: ConsoleApprovalHandler | None = None,
) -> dict[str, Any]:
    """Run one conversation turn and stream output to the terminal.

    Returns per-turn stats (event/tool tallies, artifacts, terminal reason, token
    usage) so the caller can persist a run manifest when running inside a project.
    """
    accumulated_text = ""
    current_tool = ""
    artifacts: list[str] = []
    event_counts: dict[str, int] = {}
    tool_calls: dict[str, int] = {}
    terminal_reason: str | None = None
    token_usage = {"input_tokens": 0, "output_tokens": 0}

    # Held explicitly so an early break (shutdown/interrupt) still closes the
    # generator — that's what triggers ledger closure and history write-back.
    stream = runtime.session.send(query)

    with Live(console=console, refresh_per_second=10) as live:
        if approval is not None:
            approval.live = live
        try:
            async for event in stream:
                event_counts[type(event).__name__] = event_counts.get(type(event).__name__, 0) + 1
                if shutdown and shutdown.is_shutdown_requested():
                    logger.info("Shutdown requested, stopping agent loop.")
                    break
                if isinstance(event, StreamTextEvent):
                    accumulated_text += event.text
                    live.update(Panel(Markdown(accumulated_text), title="Agent"))

                elif isinstance(event, ToolUseEvent):
                    current_tool = event.tool_name
                    tool_calls[event.tool_name] = tool_calls.get(event.tool_name, 0) + 1
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

                elif isinstance(event, UsageEvent):
                    token_usage["input_tokens"] += event.input_tokens
                    token_usage["output_tokens"] += event.output_tokens

                elif isinstance(event, CompleteEvent):
                    terminal_reason = event.terminal_reason
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

    return {
        "artifacts": artifacts,
        "event_counts": event_counts,
        "tool_calls": tool_calls,
        "terminal_reason": terminal_reason,
        "token_usage": token_usage,
    }


async def run_single(
    query: str,
    config: AgentConfig,
    persist_path: str | Path | None,
    analysis_paths: Sequence[str | Path] | None = None,
    project: Project | None = None,
) -> None:
    """One-shot mode: a single query in a fresh (or resumed) session."""
    _shutdown_manager.install()
    approval = ConsoleApprovalHandler(console)
    runtime = build_runtime(
        config,
        persist_path,
        approval_handler=approval,
        analysis_paths=analysis_paths,
        project=project,
    )
    started_at = _now_iso()
    stats: dict[str, Any] | None = None
    try:
        stats = await run_turn(runtime, query, _shutdown_manager, approval)
    finally:
        await runtime.shutdown()
    if stats is not None:
        _record_run(runtime, query, analysis_paths, stats, started_at, _now_iso())


async def run_interactive(
    config: AgentConfig,
    persist_path: str | Path | None,
    analysis_paths: Sequence[str | Path] | None = None,
    project: Project | None = None,
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
    runtime = build_runtime(
        config,
        persist_path,
        approval_handler=approval,
        analysis_paths=analysis_paths,
        project=project,
    )
    started_at = _now_iso()
    agg: dict[str, Any] = {
        "event_counts": {},
        "tool_calls": {},
        "artifacts": [],
        "terminal_reason": None,
        "token_usage": {"input_tokens": 0, "output_tokens": 0},
    }
    turn_count = 0
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
            if query.startswith(("/define", "/pref")):
                parsed = parse_memory_command(query)
                if parsed is None:
                    console.print("[dim]用法:/define 名称=定义  或  /pref 偏好描述[/dim]")
                elif runtime.memory_injector is None:
                    console.print("[dim]记忆未启用(enable_memory=False)。[/dim]")
                else:
                    console.print(
                        f"[dim]{apply_memory_command(runtime.memory_injector, parsed)}[/dim]"
                    )
                continue
            stats = await run_turn(runtime, query, _shutdown_manager, approval)
            _merge_run_stats(agg, stats)
            turn_count += 1
            console.print()
    finally:
        await runtime.shutdown()
    if turn_count > 0:
        _record_run(
            runtime,
            f"(interactive, {turn_count} turns)",
            analysis_paths,
            agg,
            started_at,
            _now_iso(),
        )


def _run_project_cli(argv: Sequence[str]) -> None:
    """`data-agent project {init,status,list,open,history}` — read-only except init."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="data-agent project",
        description="Manage local project workspaces.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="Create a project root + manifest.")
    p_init.add_argument("project_id")
    p_init.add_argument("--path", help="Project root dir (default ~/.daa/projects/<id>)")
    p_init.add_argument(
        "--authorize",
        action="append",
        default=[],
        metavar="PATH",
        help="Authorized data path (repeatable)",
    )
    p_init.add_argument("--model", default="")
    p_init.add_argument("--preset", default="")
    p_status = sub.add_parser("status", help="Show the project manifest.")
    p_status.add_argument("project_id")
    sub.add_parser("list", help="List projects.")
    p_open = sub.add_parser("open", help="Show how to run inside a project.")
    p_open.add_argument("project_id")
    p_hist = sub.add_parser("history", help="List recorded runs.")
    p_hist.add_argument("project_id")
    p_hist.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    if args.cmd == "init":
        proj = Project.init(
            args.project_id,
            path=args.path,
            authorized_paths=args.authorize,
            model=args.model,
            preset=args.preset,
        )
        console.print(f"[green]已初始化项目[/green] {proj.project_id}\n根目录: {proj.root}")
    elif args.cmd == "list":
        projects = Project.list_projects()
        if not projects:
            console.print("[dim]无项目。用 data-agent project init <id> 创建。[/dim]")
            return
        for proj in projects:
            console.print(f"- {proj.project_id}  ({len(proj.manifest.runs)} runs)  {proj.root}")
    elif args.cmd in ("status", "open", "history"):
        try:
            proj = Project.open(args.project_id)
        except (KeyError, ValueError, OSError):
            console.print(f"[red]项目不可读或不存在: {args.project_id}[/red]")
            sys.exit(1)
        if args.cmd == "status":
            console.print(
                Panel(
                    json.dumps(proj.manifest.to_dict(), ensure_ascii=False, indent=2),
                    title=f"Project {proj.project_id}",
                )
            )
        elif args.cmd == "open":
            console.print(f'运行分析: data-agent --project {proj.project_id} "你的分析问题"')
        else:  # history
            runs = proj.history()[: args.limit]
            if not runs:
                console.print("[dim]无 run 记录。[/dim]")
                return
            for run in runs:
                reason = run.terminal_reason or "?"
                console.print(
                    f"- {run.run_id[:8]}  {run.started_at}  "
                    f"({reason}, tools={sum(run.tool_calls.values())}, "
                    f"artifacts={len(run.artifacts)})"
                )


def main() -> None:
    """Main CLI entry point."""
    # `data-agent project ...` is dispatched before top-level argparse so the
    # subcommand token is not mistaken for a natural-language query.
    if len(sys.argv) > 1 and sys.argv[1] == "project":
        _run_project_cli(sys.argv[2:])
        return
    import argparse

    parser = argparse.ArgumentParser(description="Data Analysis Agent")
    parser.add_argument("query", nargs="?", help="Natural language query")
    parser.add_argument("--config", "-c", help="Path to config file")
    parser.add_argument("--model", "-m", help="Model ID")
    parser.add_argument("--max-turns", type=int, help="Maximum turns")
    parser.add_argument(
        "--preset",
        choices=["local_safe", "local_dev"],
        help="Permission preset: local_safe (deny-by-default) or local_dev (CLI-friendly)",
    )
    parser.add_argument(
        "--sensitive",
        action="store_true",
        help="Sensitive mode: suppress memory writes and trajectory input capture this run",
    )
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--persist", "-p", help="Path to JSONL message store")
    parser.add_argument(
        "--path",
        action="append",
        metavar="DIR_OR_FILE",
        help=(
            "Authorize a data file or directory for analysis (repeatable). "
            "Lets data_profile/python_analysis read absolute paths there; "
            "defaults to the current working directory."
        ),
    )
    parser.add_argument(
        "--project",
        help=(
            "Run inside project <id> (~/.daa/projects/<id>): session state lands "
            "under the project root and a run manifest is recorded."
        ),
    )
    parser.add_argument(
        "--project-path",
        help="Run inside the project whose root is this directory.",
    )
    args = parser.parse_args()

    config = AgentConfig.from_file(args.config) if args.config else AgentConfig.from_env()

    if args.model:
        config.model = args.model
    if args.max_turns:
        config.max_turns = args.max_turns
    if args.preset:
        config.permission_preset = args.preset
    if args.sensitive:
        config.sensitive_mode = True

    project: Project | None = None
    try:
        if args.project_path:
            if args.project:
                console.print(
                    "[dim]提示:同时指定 --project-path 和 --project,使用 --project-path。[/dim]"
                )
            project = Project.open_path(args.project_path)
        elif args.project:
            project = Project.open(args.project)
    except (KeyError, ValueError, OSError) as exc:
        console.print(f"[red]Error: 打开项目失败: {exc}[/red]")
        sys.exit(1)

    if project is not None and args.persist:
        console.print(
            "[dim]提示:--project 激活时忽略 --persist(会话落项目 sessions/<run_id>.jsonl)。[/dim]"
        )

    if not config.api_key:
        console.print(
            "[red]Error: ANTHROPIC_API_KEY not set.[/red]\n"
            "Set it via environment variable or config file."
        )
        sys.exit(1)

    if args.path:
        analysis_paths: list[str | Path] | None = list(args.path)
    elif project is not None and project.manifest.authorized_paths:
        analysis_paths = list(project.manifest.authorized_paths)
    else:
        analysis_paths = None

    try:
        if args.interactive or not args.query:
            asyncio.run(run_interactive(config, args.persist, analysis_paths, project))
        else:
            asyncio.run(run_single(args.query, config, args.persist, analysis_paths, project))
    finally:
        _shutdown_manager.restore()


if __name__ == "__main__":
    main()
