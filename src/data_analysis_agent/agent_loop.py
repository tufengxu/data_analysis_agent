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
from collections.abc import AsyncIterator
from typing import Any

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
from .sampling import SamplingConfig, compact_result
from .sampling.result_store import ResultStore
from .security.permissions import PermissionBehavior, PermissionEngine
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


class AgentLoopConfig:
    """Immutable configuration for the agent loop."""

    DEFAULT_SYSTEM_PROMPT = (
        "You are a data analysis assistant. You can read files, execute Python code, "
        "query data sources, and generate visualizations. Classify each request before acting: "
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

    RECOVERY_MAX_TOKENS = 64000
    MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3

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
    ):
        self.config = config
        self.registry = registry
        self.compressor = compressor or ContextCompressor()
        self.sampling_config = sampling_config or SamplingConfig()
        self.result_store = result_store
        self.store = store
        self.skill_registry = skill_registry
        self.permission_engine = permission_engine
        self.client = (
            client
            if client is not None
            else AnthropicApiClient(
                api_key=config.api_key,
                model=config.model,
            )
        )

    async def run(
        self,
        user_input: str,
    ) -> AsyncIterator[AgentEvent]:
        """Run the agent loop, yielding events as an async generator."""
        state = AgentState(
            messages=[Message(role="user", content=user_input)],
        )
        terminal: TerminalReason | None = None

        # Persist initial user message
        if self.store:
            self.store.append(state.messages[0])

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
                        recovery = self._attempt_recovery(state, e)
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
                if self.store:
                    self.store.append(assistant_msg)

                # --- Step 6-8: Tool dispatch, permission gate, execution ---
                if stop_reason == "tool_use" and tool_use_blocks:
                    yield StateChangeEvent(
                        previous_state=AgentSessionState.THINKING.name,
                        new_state=AgentSessionState.TOOL_CALLING.name,
                    )

                    tool_results = await self._execute_tools(
                        tool_use_blocks,
                        state,
                        allowed_tool_names=allowed_tool_names,
                    )
                    tool_names_by_id = {block.id: block.name for block in tool_use_blocks}

                    # Append tool results to history
                    result_blocks = [tr.to_api_dict() for tr in tool_results]
                    result_msg = Message(
                        role="user",
                        content=result_blocks,
                    )
                    state = state.with_messages(state.messages + [result_msg])
                    if self.store:
                        self.store.append(result_msg)

                    for tr in tool_results:
                        yield ToolResultEvent(
                            tool_use_id=tr.tool_use_id,
                            tool_name=tool_names_by_id.get(tr.tool_use_id, ""),
                            content=tr.content if isinstance(tr.content, str) else str(tr.content),
                            is_error=tr.is_error,
                        )

                    # Continue to next turn
                    state = state.with_turn_count(state.turn_count + 1)
                    state = state.with_transition(
                        ContinueReason.NEXT_TURN,
                    )
                    continue

                # --- Step 9: Stop condition check ---

                if stop_reason == "max_tokens":
                    recovery = self._handle_max_tokens(state)
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
            yield ErrorEvent(error=e, is_recoverable=False)
            terminal = TerminalReason.MODEL_ERROR

        finally:
            # Ledger closure: ensure every tool_use has a matching tool_result
            state = self._yield_missing_tool_results(state)

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
        """Return the best matching skill for the initial user request."""
        if not self.skill_registry or not state.messages:
            return None
        first_msg = state.messages[0]
        if first_msg.role != "user" or not isinstance(first_msg.content, str):
            return None
        return self.skill_registry.match_best(first_msg.content)

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

        If skill_registry is configured and the user's first message matches a
        skill, prepend that skill's instructions to the base system prompt.
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
    ) -> list[ToolResultBlock]:
        """Execute tools with simple serial execution."""
        results: list[ToolResultBlock] = []

        for block in tool_use_blocks:
            if allowed_tool_names is not None and block.name not in allowed_tool_names:
                results.append(
                    ToolResultBlock(
                        tool_use_id=block.id,
                        content=(
                            f"Permission denied: Tool '{block.name}' is not "
                            "available in the active tool pool."
                        ),
                        is_error=True,
                    )
                )
                continue

            tool = self.registry.get_tool(block.name)
            if tool is None:
                results.append(
                    ToolResultBlock(
                        tool_use_id=block.id,
                        content=f"Error: Tool '{block.name}' not found.",
                        is_error=True,
                    )
                )
                continue

            # Permission gate
            if self.permission_engine is not None:
                decision = self.permission_engine.check(block.name, block.input)
                if decision.behavior == PermissionBehavior.DENY:
                    results.append(
                        ToolResultBlock(
                            tool_use_id=block.id,
                            content=f"Permission denied: {decision.reason}",
                            is_error=True,
                        )
                    )
                    continue
                if decision.behavior == PermissionBehavior.ASK:
                    results.append(
                        ToolResultBlock(
                            tool_use_id=block.id,
                            content=(
                                "Permission denied: confirmation required but "
                                f"no interactive approval handler is configured. {decision.reason}"
                            ),
                            is_error=True,
                        )
                    )
                    continue

            perm = tool.check_permissions(block.input)
            if not perm.allowed:
                results.append(
                    ToolResultBlock(
                        tool_use_id=block.id,
                        content=f"Permission denied: {perm.reason}",
                        is_error=True,
                    )
                )
                continue

            # Validate input
            val = tool.validate_input(block.input)
            if not val.valid:
                results.append(
                    ToolResultBlock(
                        tool_use_id=block.id,
                        content=f"Validation error: {val.error}",
                        is_error=True,
                    )
                )
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
                results.append(
                    ToolResultBlock(
                        tool_use_id=block.id,
                        content=content,
                        is_error=tool_result.is_error,
                    )
                )
            except Exception as e:
                results.append(
                    ToolResultBlock(
                        tool_use_id=block.id,
                        content=f"Execution error: {e}",
                        is_error=True,
                    )
                )

        return results

    def _attempt_recovery(
        self,
        state: AgentState,
        error: AnthropicClientError,
    ) -> AgentState | None:
        """Attempt to recover from recoverable API errors."""
        msg = str(error).lower()
        if "prompt is too long" in msg or "too long" in msg:
            # First try collapse drain (zero cost)
            if self.compressor.collapse and self.compressor.collapse.staged_indices:
                drained = self.compressor.drain_collapse(state.messages)
                return state.with_messages(drained.messages).with_transition(
                    ContinueReason.COLLAPSE_DRAIN_RETRY,
                )
            if not state.has_attempted_reactive_compact:
                compacted = self.compressor.force_auto_compact(state.messages)
                return (
                    state.with_messages(
                        compacted.messages,
                    )
                    .with_has_attempted_reactive_compact(True)
                    .with_transition(
                        ContinueReason.REACTIVE_COMPACT_RETRY,
                    )
                )
        return None

    def _handle_max_tokens(self, state: AgentState) -> AgentState | None:
        """Handle max_output_tokens truncation: escalate or recover."""
        current_cap = state.max_output_tokens_override or self.config.max_tokens
        continuation = Message(
            role="user",
            content=(
                "Please continue from where the previous response stopped. "
                "Do not repeat completed content."
            ),
            is_meta=True,
        )
        if current_cap < self.RECOVERY_MAX_TOKENS:
            return (
                state.with_messages(
                    state.messages + [continuation],
                )
                .with_max_output_tokens_override(
                    self.RECOVERY_MAX_TOKENS,
                )
                .with_transition(ContinueReason.MAX_OUTPUT_TOKENS_ESCALATE)
            )
        if state.max_output_tokens_recovery_count < self.MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
            return (
                state.with_messages(
                    state.messages + [continuation],
                )
                .with_max_output_tokens_recovery_count(
                    state.max_output_tokens_recovery_count + 1,
                )
                .with_transition(ContinueReason.MAX_OUTPUT_TOKENS_RECOVERY)
            )
        return None

    def _yield_missing_tool_results(self, state: AgentState) -> AgentState:
        """Ledger closure: generate synthetic tool_result for orphan tool_use blocks."""
        messages = state.messages[:]
        modified = False

        # Find tool_use blocks without matching tool_result
        tool_use_ids: set[str] = set()
        tool_result_ids: set[str] = set()

        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use":
                            tool_use_ids.add(block.get("id", ""))
                        elif block.get("type") == "tool_result":
                            tool_result_ids.add(
                                block.get("tool_use_id", ""),
                            )

        orphan_ids = tool_use_ids - tool_result_ids
        if orphan_ids:
            synthetic_blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": "[Tool execution cancelled or interrupted]",
                    "is_error": True,
                }
                for tid in orphan_ids
                if tid
            ]
            if synthetic_blocks:
                messages.append(
                    Message(role="user", content=synthetic_blocks),
                )
                modified = True

        return state.with_messages(messages) if modified else state
