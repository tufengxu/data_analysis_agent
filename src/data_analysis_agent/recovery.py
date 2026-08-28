"""RecoveryPolicy: the agent loop's error/truncation recovery decisions.

Extracted from ``AgentLoop`` so the escalation ladder is testable in isolation.
Given a state (and, for model errors, the error), each method returns the next
``AgentState`` to retry from, or ``None`` to give up — pure transition decisions
with no event emission. The only I/O is one best-effort summarizer model call,
which degrades to ``None`` on any failure.

The ladder the loop drives:

* recoverable "prompt too long" → drain staged collapse first (zero cost); else
  one reactive auto-compact, fed an LLM summary of the history being dropped.
* ``max_output_tokens`` truncation → escalate the cap once, then a bounded number
  of continuation retries, then give up (the loop treats ``None`` as COMPLETED).

The compression *mechanism* stays in ``ContextCompressor``; this module only owns
the *policy* — which lever to pull, in what order, and when to stop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .context.compression import ContextCompressor, message_to_text
from .protocol.client import AnthropicApiClient, AnthropicClientError
from .protocol.messages import TextBlock
from .state_machine import AgentState, ContinueReason, Message

logger = logging.getLogger(__name__)


def _head_tail(text: str, limit: int) -> str:
    """Keep the head (early schema conclusions) AND tail (recent results).

    Replaces the old tail-only slice, which silently cut off the beginning of
    the dropped span — exactly where file schemas and dataset conclusions
    live in a data-analysis session.
    """
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head
    return text[:head] + "\n…[中段省略]…\n" + text[-tail:]


class RecoveryPolicy:
    """Decides how to recover from recoverable model errors and truncation."""

    RECOVERY_MAX_TOKENS = 64000
    MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
    # Loop-level retries for transient API errors (429/timeout/overloaded) after
    # the streaming layer's own retries are exhausted.
    TRANSIENT_RECOVERY_LIMIT = 3
    TRANSIENT_BACKOFF_CAP = 10.0

    # Cap on the history digest fed to the summarizer model.
    SUMMARIZE_INPUT_CHARS = 24_000
    SUMMARIZE_MAX_TOKENS = 800

    def __init__(
        self,
        compressor: ContextCompressor,
        client: AnthropicApiClient,
        max_tokens: int,
        sleep: Callable[[float], Awaitable[None]] | Any = asyncio.sleep,
        data_state_provider: Callable[[], Awaitable[str | None]] | None = None,
    ) -> None:
        self.compressor = compressor
        self.client = client
        self.max_tokens = max_tokens
        # D4: runtime-injected data state (kernel variable map + live recall
        # ids). Dependency inversion — recovery imports nothing new; a missing
        # or failing provider just omits the data-state sections.
        self.data_state_provider = data_state_provider
        self._sleep = sleep

    async def attempt_recovery(
        self,
        state: AgentState,
        error: AnthropicClientError,
    ) -> AgentState | None:
        """Attempt to recover from recoverable API errors.

        Two mutually-exclusive paths: a length error ("prompt too long") goes
        through the collapse/compact ladder; any other recoverable error is a
        transient API error (429/timeout/overloaded/connection — the client only
        marks these and length errors recoverable) handled by bounded backoff.
        """
        msg = str(error).lower()
        if "prompt is too long" in msg or "too long" in msg:
            return await self._recover_prompt_too_long(state)
        return await self._recover_transient(state)

    async def _recover_prompt_too_long(self, state: AgentState) -> AgentState | None:
        """Length-error ladder: drain staged collapse (zero cost), else one
        reactive auto-compact fed an LLM summary of the dropped history."""
        # First try collapse drain (zero cost)
        if self.compressor.collapse and self.compressor.collapse.staged_indices:
            drained = self.compressor.drain_collapse(state.messages)
            return state.with_messages(drained.messages).with_transition(
                ContinueReason.COLLAPSE_DRAIN_RETRY,
            )
        if not state.has_attempted_reactive_compact:
            summary = await self._summarize_for_compact(state.messages)
            compacted = self.compressor.force_auto_compact(state.messages, summary=summary)
            messages = compacted.messages
            # D4/P2-2: re-inject the data state right after compaction so the
            # model still knows which datasets exist and how to re-fetch them.
            data_state = await self._current_data_state()
            if data_state:
                messages = messages + [
                    Message(
                        role="user",
                        content="[数据状态(压缩后重注入)]\n" + data_state,
                        is_meta=True,
                    )
                ]
            return (
                state.with_messages(
                    messages,
                )
                .with_has_attempted_reactive_compact(True)
                .with_transition(
                    ContinueReason.REACTIVE_COMPACT_RETRY,
                )
            )
        return None

    async def _recover_transient(self, state: AgentState) -> AgentState | None:
        """Transient API error (429/timeout/overloaded): bounded loop-level
        backoff retry. The streaming layer already retried a few times; this is
        the last-chance retry before giving up (None → MODEL_ERROR)."""
        if state.transient_recovery_count < self.TRANSIENT_RECOVERY_LIMIT:
            delay = min(2**state.transient_recovery_count, self.TRANSIENT_BACKOFF_CAP)
            await self._sleep(delay)
            return state.with_transient_recovery_count(
                state.transient_recovery_count + 1
            ).with_transition(ContinueReason.TRANSIENT_RETRY)
        return None

    async def _current_data_state(self) -> str | None:
        """Pull the runtime data state via the injected provider (fail-open)."""
        if self.data_state_provider is None:
            return None
        try:
            return await self.data_state_provider()
        except Exception as e:
            logger.debug("data_state_provider failed, omitting data state: %r", e)
            return None

    async def _summarize_for_compact(self, messages: list[Message]) -> str | None:
        """Produce an LLM summary of the messages auto-compact will drop.

        Best-effort: any failure (mock client without call_model, API error)
        degrades to None, and AutoCompactStrategy falls back to its local
        placeholder marker. D4: the digest keeps BOTH the head (early schema
        conclusions) and the tail (recent tool results); the prompt is a
        structured handoff template with fixed sections, fed with the runtime
        data state when a provider is wired.
        """
        dropped = self.compressor.auto_compact.preview_removed(messages)
        if not dropped:
            return None
        digest = "\n\n".join(message_to_text(m) for m in dropped)
        digest = _head_tail(digest, self.SUMMARIZE_INPUT_CHARS)
        prompt = (
            "以下是一段数据分析对话中即将被压缩丢弃的历史(头尾拼接,中段已省略)。"
            "请输出结构化交接摘要(handoff),严格按分节,每节至多 3 行,总长不超过 500 token:\n"
            "1. 任务目标与硬约束\n"
            "2. 已读文件与各表 schema(列名/类型/行数)\n"
            "3. 现存数据变量(核对下方运行时数据态后引用)\n"
            "4. 关键数值结论与已确认口径\n"
            "5. 未决事项\n"
            "6. 可回取 result_id(引用下方运行时数据态)\n"
            "没有内容的分节写「无」;不得编造未出现的信息;"
            "这是持续对话的中间压缩,不要做收尾总结。\n"
        )
        data_state = await self._current_data_state()
        if data_state:
            prompt += "\n[运行时数据态(供第 3/6 节)]\n" + data_state + "\n"
        prompt += "\n" + digest
        try:
            response = await self.client.call_model(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.SUMMARIZE_MAX_TOKENS,
            )
        except Exception as e:
            # Best-effort by design, but repeated failures must stay visible.
            logger.debug("history summarization failed, using local fallback: %r", e)
            return None
        parts = [
            block.text for block in response.content if isinstance(block, TextBlock) and block.text
        ]
        text = "\n".join(parts).strip()
        return text or None

    def handle_max_tokens(self, state: AgentState) -> AgentState | None:
        """Handle max_output_tokens truncation: escalate or recover."""
        current_cap = state.max_output_tokens_override or self.max_tokens
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
