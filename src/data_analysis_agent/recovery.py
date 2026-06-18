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

import logging

from .context.compression import ContextCompressor, message_to_text
from .protocol.client import AnthropicApiClient, AnthropicClientError
from .protocol.messages import TextBlock
from .state_machine import AgentState, ContinueReason, Message

logger = logging.getLogger(__name__)


class RecoveryPolicy:
    """Decides how to recover from recoverable model errors and truncation."""

    RECOVERY_MAX_TOKENS = 64000
    MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3

    # Cap on the history digest fed to the summarizer model.
    SUMMARIZE_INPUT_CHARS = 24_000
    SUMMARIZE_MAX_TOKENS = 800

    def __init__(
        self,
        compressor: ContextCompressor,
        client: AnthropicApiClient,
        max_tokens: int,
    ) -> None:
        self.compressor = compressor
        self.client = client
        self.max_tokens = max_tokens

    async def attempt_recovery(
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
                summary = await self._summarize_for_compact(state.messages)
                compacted = self.compressor.force_auto_compact(state.messages, summary=summary)
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

    async def _summarize_for_compact(self, messages: list[Message]) -> str | None:
        """Produce an LLM summary of the messages auto-compact will drop.

        Best-effort: any failure (mock client without call_model, API error)
        degrades to None, and AutoCompactStrategy falls back to its local
        placeholder marker.
        """
        dropped = self.compressor.auto_compact.preview_removed(messages)
        if not dropped:
            return None
        digest = "\n\n".join(message_to_text(m) for m in dropped)
        digest = digest[-self.SUMMARIZE_INPUT_CHARS :]
        prompt = (
            "以下是一段数据分析对话中即将被压缩丢弃的历史。请用不超过 500 token 输出"
            "结构化摘要,只保留:已读取的文件与数据 schema、关键数值结论、"
            "用户明确的偏好或约束、尚未完成的事项。\n\n" + digest
        )
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
