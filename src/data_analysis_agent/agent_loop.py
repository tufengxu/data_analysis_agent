"""AgentLoop core engine: ReAct-pattern while loop.

The model decides WHAT to do; the harness decides HOW MUCH.
Only ~1.6% of the logic is AI (model call); 98.4% is deterministic infrastructure.

Pipeline per turn (9 steps):
1. Settings resolution
2. Mutable state initialization
3. Context assembly
4. Pre-model context shapers (compression)
5. Model call (the only non-deterministic step)
6. Tool-use dispatch
7. Permission gate
8. Tool execution & result collection
9. Stop condition check
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .context.compression import ContextCompressor, estimate_tokens, message_to_text
from .events import (
    AgentEvent,
    CompleteEvent,
    ErrorEvent,
    RequestStartEvent,
    StateChangeEvent,
    StreamTextEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from .persistence import MessageStore
from .protocol.client import AnthropicApiClient, AnthropicClientError
from .protocol.messages import (
    ContentBlock,
    ModelResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from .recovery import RecoveryPolicy
from .sampling import SamplingConfig, compact_result
from .sampling.result_store import ResultStore
from .security.permissions import PermissionEngine
from .security.tool_gate import ToolGate
from .skills.base import Skill
from .skills.registry import SkillRegistry
from .state_machine import (
    AgentSessionState,
    AgentState,
    ContinueReason,
    Message,
    TerminalReason,
)
from .tools.base import Tool, ToolResult
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Async callback: (tool_name, tool_input) -> approved? Used for ASK permissions.
ApprovalHandler = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class ToolExecutionRecord:
    """Outcome of one tool invocation: the API result block plus side artifacts."""

    result: ToolResultBlock
    artifacts: list[str] = field(default_factory=list)


def ensure_tool_ledger_closed(messages: list[Message]) -> list[Message]:
    """Insert synthetic tool_results so every tool_use is answered in place.

    The Messages API requires each assistant tool_use to be covered by a
    tool_result in the immediately following user message. This walks the
    history (e.g. a resumed session that was interrupted mid-tools) and
    patches gaps positionally rather than appending at the end.
    """

    def _use_ids(msg: Message) -> set[str]:
        if msg.role != "assistant" or not isinstance(msg.content, list):
            return set()
        return {
            str(block.get("id", ""))
            for block in msg.content
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
        }

    def _result_ids(msg: Message) -> set[str]:
        if msg.role != "user" or not isinstance(msg.content, list):
            return set()
        return {
            str(block.get("tool_use_id", ""))
            for block in msg.content
            if isinstance(block, dict) and block.get("type") == "tool_result"
        }

    def _synthetic(missing: set[str]) -> list[dict[str, Any]]:
        return [
            {
                "type": "tool_result",
                "tool_use_id": tid,
                "content": "[Tool execution cancelled or interrupted]",
                "is_error": True,
            }
            for tid in sorted(missing)
        ]

    closed: list[Message] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        closed.append(msg)
        use_ids = _use_ids(msg)
        if use_ids:
            next_msg = messages[i + 1] if i + 1 < len(messages) else None
            covered = _result_ids(next_msg) if next_msg is not None else set()
            missing = use_ids - covered
            if missing and next_msg is not None and covered:
                # Partial coverage: merge synthetic blocks into the existing result message.
                assert isinstance(next_msg.content, list)
                closed.append(
                    Message(
                        role="user",
                        content=list(next_msg.content) + _synthetic(missing),
                        is_meta=next_msg.is_meta,
                    )
                )
                i += 2
                continue
            if missing:
                closed.append(Message(role="user", content=_synthetic(missing)))
        i += 1
    return closed


class AgentLoopConfig:
    """Immutable configuration for the agent loop."""

    DEFAULT_SYSTEM_PROMPT = (
        "You are a data analysis assistant. You can read files, execute Python code, "
        "query data sources, generate visualizations, and produce H5 HTML analysis "
        "reports with ECharts charts (html_report). Classify each request before acting: "
        "answer simple questions directly, use one tool directly for simple single-tool tasks, "
        "and write a concise plan before executing complex multi-step tasks. "
        "When a matching skill is active, follow that skill before generic reasoning or tools."
    )

    def __init__(
        self,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_turns: int = 15,
        max_tokens: int = 8192,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.model = model
        self.api_key = api_key


class AgentLoop:
    """Core ReAct agent loop engine."""

    def __init__(
        self,
        config: AgentLoopConfig,
        registry: ToolRegistry,
        compressor: ContextCompressor | None = None,
        store: MessageStore | None = None,
        skill_registry: SkillRegistry | None = None,
        permission_engine: PermissionEngine | None = None,
        client: AnthropicApiClient | None = None,
        sampling_config: SamplingConfig | None = None,
        result_store: ResultStore | None = None,
        approval_handler: ApprovalHandler | None = None,
        artifact_store: ArtifactStore | None = None,
        memory_injector: Callable[[str], str] | None = None,
        memory_recorder: Callable[[str, dict[str, Any], dict[str, Any]], None] | None = None,
    ):
        self.config = config
        self.registry = registry
        self.compressor = compressor or ContextCompressor()
        self.sampling_config = sampling_config or SamplingConfig()
        self.result_store = result_store
        self.store = store
        self.skill_registry = skill_registry
        self.permission_engine = permission_engine
        self.tool_gate = ToolGate(permission_engine)
        self.approval_handler = approval_handler
        self.artifact_store = artifact_store
        # Domain-memory callbacks (decoupled like approval_handler): inject
        # recalls into the system prompt; record dataset profiles on tool runs.
        self.memory_injector = memory_injector
        self.memory_recorder = memory_recorder
        # Final message list of the most recent run(), including ledger closure.
        # AgentSession reads this to carry history across turns.
        self.last_final_messages: list[Message] = []
        self.client = (
            client
            if client is not None
            else AnthropicApiClient(
                api_key=config.api_key,
                model=config.model,
            )
        )
        # Recovery decisions (error/truncation escalation ladder) live in their
        # own testable policy; it shares the loop's compressor + client instances.
        self.recovery_policy = RecoveryPolicy(self.compressor, self.client, config.max_tokens)

    async def run(
        self,
        user_input: str,
        history: list[Message] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run one turn of the agent loop, yielding events as an async generator.

        Args:
            user_input: The new user message for this turn.
            history: Prior conversation messages (e.g. from AgentSession).
                Defaults to None for backward-compatible single-shot runs.
        """
        base = list(history) if history else []
        new_user_msg = Message(role="user", content=user_input)
        state = AgentState(messages=[*base, new_user_msg])
        terminal: TerminalReason | None = None

        # Persist only the new user message; history is already on disk.
        if self.store is not None:
            self.store.append(new_user_msg)

        try:
            while True:
                # --- Step 1-3: Settings + State init + Context assembly ---
                state = state.with_transition(None)

                if state.turn_count >= self.config.max_turns:
                    terminal = TerminalReason.MAX_TURNS
                    break

                # --- Step 4: Context shapers (compression) ---

                # Apply compression if needed
                self.compressor.stage_collapse(state.messages)
                compressed = self.compressor.compress(state.messages)
                working_messages = compressed.messages

                anthropic_messages = [m.to_anthropic_format() for m in working_messages]

                active_skill = self._match_active_skill(state)
                available_tools = self._assemble_tool_pool(active_skill)
                allowed_tool_names = {tool.name for tool in available_tools}
                tools = [t.to_anthropic_tool() for t in available_tools]
                max_tokens = state.max_output_tokens_override or self.config.max_tokens

                # --- Step 5: Model call (non-deterministic) ---
                yield RequestStartEvent(
                    model_id=self.client.model,
                    max_output_tokens=max_tokens,
                    turn_count=state.turn_count,
                    active_skill=active_skill.name if active_skill else None,
                )
                yield StateChangeEvent(
                    previous_state=AgentSessionState.IDLE.name,
                    new_state=AgentSessionState.THINKING.name,
                )

                assistant_content: list[dict[str, Any]] = []
                tool_use_blocks: list[ToolUseBlock] = []
                stop_reason: str | None = None

                # Resolve system prompt with skill instructions if applicable
                system_prompt = self._resolve_system_prompt(state, active_skill)

                try:
                    async for item in self.client.stream_model(
                        messages=anthropic_messages,
                        system=system_prompt,
                        tools=tools if tools else None,
                        max_tokens=max_tokens,
                    ):
                        if isinstance(item, ContentBlock):
                            if isinstance(item, ToolUseBlock):
                                assistant_content.append(item.to_api_dict())
                                tool_use_blocks.append(item)
                                yield ToolUseEvent(
                                    tool_use_id=item.id,
                                    tool_name=item.name,
                                    parameters=item.input,
                                    parameters_complete=True,
                                )
                            elif isinstance(item, TextBlock):
                                assistant_content.append(item.to_api_dict())
                                yield StreamTextEvent(
                                    text=item.text,
                                    content_block_id=getattr(item, "id", None),
                                )
                        elif isinstance(item, ModelResponse):
                            stop_reason = item.stop_reason
                            if item.usage:
                                yield UsageEvent(
                                    input_tokens=item.usage.get("input_tokens", 0),
                                    output_tokens=item.usage.get("output_tokens", 0),
                                )
                            # Use content blocks from final response if streaming
                            # didn't already accumulate them
                            if item.content and not assistant_content:
                                assistant_content = [b.to_api_dict() for b in item.content]
                                tool_use_blocks = [
                                    b for b in item.content if isinstance(b, ToolUseBlock)
                                ]

                except AnthropicClientError as e:
                    if e.is_recoverable:
                        # Attempt recovery
                        recovery = await self.recovery_policy.attempt_recovery(state, e)
                        if recovery:
                            state = recovery
                            continue
                    yield ErrorEvent(
                        error=e,
                        is_recoverable=False,
                        withheld=False,
                    )
                    terminal = TerminalReason.MODEL_ERROR
                    break

                # Append assistant message to history
                assistant_msg = Message(
                    role="assistant",
                    content=assistant_content,
                )
                state = state.with_messages(state.messages + [assistant_msg])
                if self.store is not None:
                    self.store.append(assistant_msg)

                # --- Step 6-8: Tool dispatch, permission gate, execution ---
                if stop_reason == "tool_use" and tool_use_blocks:
                    yield StateChangeEvent(
                        previous_state=AgentSessionState.THINKING.name,
                        new_state=AgentSessionState.TOOL_CALLING.name,
                    )

                    records: list[ToolExecutionRecord] = []
                    async for exec_item in self._execute_tools(
                        tool_use_blocks,
                        state,
                        allowed_tool_names=allowed_tool_names,
                    ):
                        if isinstance(exec_item, ToolExecutionRecord):
                            records.append(exec_item)
                        else:
                            yield exec_item
                    tool_names_by_id = {block.id: block.name for block in tool_use_blocks}

                    # Append tool results to history
                    result_blocks = [rec.result.to_api_dict() for rec in records]
                    result_msg = Message(
                        role="user",
                        content=result_blocks,
                    )
                    state = state.with_messages(state.messages + [result_msg])
                    if self.store is not None:
                        self.store.append(result_msg)

                    for rec in records:
                        tr = rec.result
                        yield ToolResultEvent(
                            tool_use_id=tr.tool_use_id,
                            tool_name=tool_names_by_id.get(tr.tool_use_id, ""),
                            content=tr.content if isinstance(tr.content, str) else str(tr.content),
                            is_error=tr.is_error,
                            artifacts=tuple(rec.artifacts),
                        )

                    # Continue to next turn
                    state = state.with_turn_count(state.turn_count + 1)
                    state = state.with_transition(
                        ContinueReason.NEXT_TURN,
                    )
                    continue

                # --- Step 9: Stop condition check ---

                if stop_reason == "max_tokens":
                    recovery = self.recovery_policy.handle_max_tokens(state)
                    if recovery:
                        state = recovery
                        continue
                    terminal = TerminalReason.COMPLETED
                    break

                if stop_reason == "end_turn":
                    terminal = TerminalReason.COMPLETED
                    break

                # Unknown stop reason: treat as completed
                terminal = TerminalReason.COMPLETED
                break

        except asyncio.CancelledError:
            terminal = TerminalReason.ABORTED_STREAMING
            raise
        except Exception as e:
            # Local bugs land here too (not just model errors) — keep the stack
            # so they aren't misattributed to the model in trajectory analysis.
            logger.exception("unexpected error in agent loop")
            yield ErrorEvent(error=e, is_recoverable=False)
            terminal = TerminalReason.MODEL_ERROR

        finally:
            # Ledger closure: ensure every tool_use has a matching tool_result.
            # Captured even on cancellation so AgentSession keeps a valid history.
            pre_closure = state.messages
            state = self._yield_missing_tool_results(state)
            self.last_final_messages = state.messages
            # An interrupted run leaves its orphan at the tail; persist the
            # synthetic closure so the on-disk ledger stays replayable.
            if self.store is not None and len(state.messages) > len(pre_closure):
                with contextlib.suppress(OSError):
                    if state.messages[: len(pre_closure)] == pre_closure:
                        for closure_msg in state.messages[len(pre_closure) :]:
                            self.store.append(closure_msg)
                    else:
                        # Mid-list insertion (malformed input history): appends
                        # cannot represent it — atomically rewrite the ledger.
                        self.store.rewrite(state.messages)

        final_text = ""
        if state.messages:
            last = state.messages[-1]
            if last.role == "assistant" and isinstance(last.content, list):
                final_text = " ".join(
                    b.get("text", "") for b in last.content if b.get("type") == "text"
                )

        yield CompleteEvent(
            terminal_reason=terminal.name if terminal else "UNKNOWN",
            final_text=final_text,
        )
        yield StateChangeEvent(
            previous_state=AgentSessionState.THINKING.name,
            new_state=AgentSessionState.COMPLETE.name,
        )

    def _match_active_skill(self, state: AgentState) -> Skill | None:
        """Return the best matching skill for the latest real user request.

        Multi-turn sessions carry history, so routing must follow the most
        recent user message (skipping tool_result carriers and meta nudges),
        not the first message of the conversation.
        """
        if not self.skill_registry:
            return None
        for msg in reversed(state.messages):
            if msg.role == "user" and isinstance(msg.content, str) and not msg.is_meta:
                return self.skill_registry.match_best(msg.content)
        return None

    def _assemble_tool_pool(self, active_skill: Skill | None = None) -> list[Tool]:
        """Assemble tools, narrowing to active skill's allowlist when present."""
        tools = self.registry.assemble_tool_pool()
        if active_skill and active_skill.allowed_tools:
            allowed = set(active_skill.allowed_tools)
            tools = [tool for tool in tools if tool.name in allowed]
        return tools

    def _resolve_system_prompt(
        self,
        state: AgentState,
        active_skill: Skill | None = None,
    ) -> str | None:
        """Resolve final system prompt, injecting skill instructions if matched.

        If skill_registry is configured and the latest real user message
        matches a skill (see _match_active_skill), append that skill's
        instructions to the base system prompt.
        """
        base = self.config.system_prompt or ""

        if active_skill:
            skill_header = (
                f"\n\n## Active Skill: {active_skill.name}\n\n"
                f"{active_skill.instructions}\n\n"
                "Skill priority rule: this skill is available and must be used "
                "before general-purpose tools or generic reasoning. Only use the "
                "tools listed for this skill unless the user explicitly asks for "
                "a capability outside the skill scope."
            )
            base = base + skill_header

        if self.memory_injector is not None:
            query = next(
                (
                    m.content
                    for m in reversed(state.messages)
                    if m.role == "user" and isinstance(m.content, str) and not m.is_meta
                ),
                None,
            )
            if query:
                memory_text = self.memory_injector(query)
                if memory_text:
                    base = base + memory_text

        return base if base else None

    def _context_pressure(self, messages: list[Message]) -> float:
        """Fraction of the token budget currently used (clamped to [0, 1])."""
        budget = self.compressor.budget_tokens or 1
        total = sum(estimate_tokens(message_to_text(m)) for m in messages)
        return min(1.0, max(0.0, total / budget))

    async def _execute_tools(
        self,
        tool_use_blocks: list[ToolUseBlock],
        state: AgentState,
        allowed_tool_names: set[str] | None = None,
    ) -> AsyncIterator[AgentEvent | ToolExecutionRecord]:
        """Execute tools serially, yielding interleaved events and outcomes.

        Yields AgentEvent items (e.g. AWAITING_CONFIRMATION transitions) in real
        time, plus one ToolExecutionRecord per tool_use block. The caller
        re-yields events and collects records.
        """

        def _error(block_id: str, content: str) -> ToolExecutionRecord:
            return ToolExecutionRecord(
                result=ToolResultBlock(tool_use_id=block_id, content=content, is_error=True)
            )

        for block in tool_use_blocks:
            if allowed_tool_names is not None and block.name not in allowed_tool_names:
                yield _error(
                    block.id,
                    f"Permission denied: Tool '{block.name}' is not "
                    "available in the active tool pool.",
                )
                continue

            tool = self.registry.get_tool(block.name)
            if tool is None:
                yield _error(block.id, f"Error: Tool '{block.name}' not found.")
                continue

            # Permission gate: engine policy first (ASK interaction is the loop's),
            # then tool self-check + validation — same ordering as before.
            decision = self.tool_gate.decide(tool, block.input)
            if decision.verdict == "deny":
                yield _error(block.id, decision.message)
                continue
            if decision.verdict == "ask":
                if self.approval_handler is None:
                    yield _error(
                        block.id,
                        "Permission denied: confirmation required but "
                        f"no interactive approval handler is configured. {decision.message}",
                    )
                    continue
                yield StateChangeEvent(
                    previous_state=AgentSessionState.TOOL_CALLING.name,
                    new_state=AgentSessionState.AWAITING_CONFIRMATION.name,
                    reason=f"{block.name}: {decision.message}",
                )
                approved = await self.approval_handler(block.name, block.input)
                yield StateChangeEvent(
                    previous_state=AgentSessionState.AWAITING_CONFIRMATION.name,
                    new_state=AgentSessionState.TOOL_CALLING.name,
                    reason="approved" if approved else "denied by user",
                )
                if not approved:
                    yield _error(
                        block.id,
                        f"Permission denied: user declined to run '{block.name}'.",
                    )
                    continue

            validation_error = self.tool_gate.validate(tool, block.input)
            if validation_error is not None:
                yield _error(block.id, validation_error)
                continue

            # Execute
            try:
                tool_result: ToolResult = await tool.call(block.input)
                pressure = self._context_pressure(state.messages)
                content, was_compacted = compact_result(
                    tool_result.content,
                    tool.max_result_size_chars,
                    self.sampling_config,
                    pressure,
                )
                if was_compacted and self.result_store is not None:
                    stored = self.result_store.put(
                        block.id, tool_result.content, {"tool": block.name}
                    )
                    if stored:
                        content += (
                            "\n\n[完整结果已缓存。回取: retrieve_result("
                            f'result_id="{block.id}", offset=0, limit=50)]'
                        )
                artifacts = self._persist_artifacts(block.id, tool_result)
                if artifacts:
                    content += "\n\n[产物已保存: " + ", ".join(artifacts) + "]"
                if self.memory_recorder is not None:
                    try:
                        self.memory_recorder(block.name, block.input, tool_result.metadata or {})
                    except Exception as e:
                        # Side channel: never break tool execution, but a silently
                        # failing recorder means memory never fills — make it visible.
                        logger.warning("memory_recorder failed for %s: %r", block.name, e)
                yield ToolExecutionRecord(
                    result=ToolResultBlock(
                        tool_use_id=block.id,
                        content=content,
                        is_error=tool_result.is_error,
                    ),
                    artifacts=artifacts,
                )
            except Exception as e:
                yield _error(block.id, f"Execution error: {e}")

    def _persist_artifacts(self, tool_use_id: str, tool_result: ToolResult) -> list[str]:
        """Surface user-facing files a tool produced.

        Two channels: ``metadata["artifact_paths"]`` declares files the tool
        already wrote itself (e.g. HTML reports); ``metadata["images"]``
        carries base64 images from the sandbox that need persisting here.
        """
        metadata = tool_result.metadata or {}
        if not metadata:
            return []
        paths: list[str] = []

        declared = metadata.get("artifact_paths")
        if isinstance(declared, list):
            paths.extend(
                entry for entry in declared if isinstance(entry, str) and Path(entry).exists()
            )

        images = metadata.get("images")
        if self.artifact_store is None or not isinstance(images, list):
            return paths
        for idx, img in enumerate(images):
            if not isinstance(img, dict):
                continue
            existing = img.get("path")
            if isinstance(existing, str) and self._already_delivered(existing):
                paths.append(existing)
                continue
            saved = self.artifact_store.save_image(
                f"{tool_use_id}_{idx}",
                str(img.get("format", "png")),
                str(img.get("data", "")),
            )
            if saved is not None:
                paths.append(str(saved))
        return paths

    def _already_delivered(self, path_str: str) -> bool:
        """True if the image already lives inside the artifact dir (no copy needed)."""
        assert self.artifact_store is not None
        try:
            path = Path(path_str).resolve()
            return path.exists() and path.is_relative_to(self.artifact_store.dir.resolve())
        except OSError:
            return False

    def _yield_missing_tool_results(self, state: AgentState) -> AgentState:
        """Ledger closure: synthesize tool_results for orphan tool_use blocks."""
        closed = ensure_tool_ledger_closed(state.messages)
        return state.with_messages(closed) if len(closed) != len(state.messages) else state
