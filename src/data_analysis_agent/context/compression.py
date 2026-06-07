"""Five-level context compression pipeline.

Mirrors Claude Code's pre-model context shapers:
- L1 Budget Reduction: per-message size cap
- L2 Snip: trim oldest messages beyond time window
- L3 Microcompact: fine-grained message folding
- L4 Context Collapse: staged reduction candidates drain on 413
- L5 Auto-Compact: model-generated summary (last resort)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..state_machine import Message

# Heuristic: English/code ~1 token per 4 chars
TOKENS_PER_CHAR = 0.25


def estimate_tokens(text: str) -> int:
    """Rough token count estimate."""
    return int(len(text) * TOKENS_PER_CHAR)


def message_to_text(msg: Message) -> str:
    """Convert message content to plain text for token estimation."""
    if isinstance(msg.content, str):
        return msg.content
    parts = []
    for block in msg.content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, str):
                    parts.append(content)
            elif block.get("type") == "tool_use":
                parts.append(f"tool_use:{block.get('name', '')}")
    return "\n".join(parts)


@dataclass
class CompressionResult:
    """Outcome of a compression attempt."""

    messages: list[Message]
    compressed: bool = False
    strategy_name: str = ""
    tokens_saved: int = 0


class CompressionStrategy(Protocol):
    """Protocol for compression strategies."""

    def apply(self, messages: list[Message], budget: int) -> CompressionResult: ...


class BudgetReductionStrategy:
    """L1: Cap individual message size.

    Always active. Messages exceeding max_chars are truncated
    with a preview + reference note.
    """

    DEFAULT_MAX_CHARS = 50_000

    def __init__(self, max_chars: int | None = None) -> None:
        self.max_chars = max_chars or self.DEFAULT_MAX_CHARS

    def apply(self, messages: list[Message], budget: int) -> CompressionResult:
        compressed = False
        tokens_saved = 0
        result: list[Message] = []

        for msg in messages:
            text = message_to_text(msg)
            if len(text) > self.max_chars:
                truncated = text[: self.max_chars]
                saved_text = text[self.max_chars :]
                tokens_saved += estimate_tokens(saved_text)

                if isinstance(msg.content, str):
                    new_content = truncated + (f"\n... [truncated from {len(text)} chars]")
                    result.append(Message(role=msg.role, content=new_content))
                else:
                    # For structured content, rebuild with truncated text blocks
                    new_blocks: list[dict[str, Any]] = []
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            new_blocks.append(
                                {
                                    "type": "text",
                                    "text": truncated
                                    + (f"\n... [truncated from {len(text)} chars]"),
                                }
                            )
                        else:
                            new_blocks.append(block)
                    result.append(Message(role=msg.role, content=new_blocks))
                compressed = True
            else:
                result.append(msg)

        return CompressionResult(
            messages=result,
            compressed=compressed,
            strategy_name="budget_reduction",
            tokens_saved=tokens_saved,
        )


class SnipStrategy:
    """L2: Trim oldest messages beyond a count window.

    Removes earliest messages when total exceeds max_messages.
    Preserves system-level and most recent context.
    """

    DEFAULT_MAX_MESSAGES = 40

    def __init__(self, max_messages: int | None = None) -> None:
        self.max_messages = max_messages or self.DEFAULT_MAX_MESSAGES

    def apply(self, messages: list[Message], budget: int) -> CompressionResult:
        if len(messages) <= self.max_messages:
            return CompressionResult(messages=messages)

        keep = messages[-self.max_messages :]
        removed = messages[: -self.max_messages]
        saved = sum(estimate_tokens(message_to_text(m)) for m in removed)

        return CompressionResult(
            messages=keep,
            compressed=True,
            strategy_name="snip",
            tokens_saved=saved,
        )


class MicrocompactStrategy:
    """L3: Fine-grained message folding.

    Combines adjacent short user messages to reduce overhead.
    Lightweight local transformation, no API calls.
    """

    def apply(self, messages: list[Message], budget: int) -> CompressionResult:
        if len(messages) < 3:
            return CompressionResult(messages=messages)

        result: list[Message] = []
        i = 0
        saved = 0
        compressed = False

        while i < len(messages):
            msg = messages[i]
            # Try to merge adjacent same-role text messages
            if (
                i + 1 < len(messages)
                and msg.role == messages[i + 1].role
                and isinstance(msg.content, str)
                and isinstance(messages[i + 1].content, str)
                and len(msg.content) < 200
                and len(messages[i + 1].content) < 200
            ):
                next_msg = messages[i + 1]
                assert isinstance(next_msg.content, str)
                merged = Message(
                    role=msg.role,
                    content=msg.content + "\n" + next_msg.content,
                )
                # Approximate savings from reduced message overhead
                saved += 20
                result.append(merged)
                i += 2
                compressed = True
            else:
                result.append(msg)
                i += 1

        return CompressionResult(
            messages=result,
            compressed=compressed,
            strategy_name="microcompact",
            tokens_saved=saved,
        )


class ContextCollapseStrategy:
    """L4: Staged reduction candidates.

    During normal operation, marks candidate messages for reduction.
    When a 413 (prompt too long) occurs, drains staged candidates
    at zero API cost.
    """

    def __init__(self) -> None:
        self.staged_indices: set[int] = set()

    def stage_candidates(self, messages: list[Message]) -> None:
        """Mark old non-system messages as reduction candidates."""
        if len(messages) <= 4:
            return
        # Stage middle-aged messages (not newest 2, not oldest 1)
        for idx in range(1, len(messages) - 2):
            self.staged_indices.add(idx)

    def apply(self, messages: list[Message], budget: int) -> CompressionResult:
        if not self.staged_indices:
            return CompressionResult(messages=messages)

        result: list[Message] = []
        saved = 0
        for idx, msg in enumerate(messages):
            if idx in self.staged_indices:
                saved += estimate_tokens(message_to_text(msg))
                # Replace with compact summary marker
                result.append(
                    Message(
                        role=msg.role,
                        content="[Earlier context collapsed]",
                        is_meta=True,
                    )
                )
            else:
                result.append(msg)

        self.staged_indices.clear()
        return CompressionResult(
            messages=result,
            compressed=True,
            strategy_name="context_collapse",
            tokens_saved=saved,
        )


class AutoCompactStrategy:
    """L5: Model-generated summary (last resort).

    This strategy is a placeholder that signals the need for
    a sub-agent summary. In a full implementation it would fork
    a compact agent to summarize conversation history.
    """

    def apply(self, messages: list[Message], budget: int) -> CompressionResult:
        # Placeholder: aggressively truncate oldest messages
        if len(messages) <= 2:
            return CompressionResult(messages=messages)

        # Keep first (system-ish) and last 2 messages
        keep = [messages[0]] + messages[-2:]
        removed = messages[1:-2]
        saved = sum(estimate_tokens(message_to_text(m)) for m in removed)

        summary_msg = Message(
            role="user",
            content=f"[Previous {len(removed)} messages summarized]",
            is_meta=True,
        )
        result = [keep[0], summary_msg] + keep[1:]

        return CompressionResult(
            messages=result,
            compressed=True,
            strategy_name="auto_compact",
            tokens_saved=saved,
        )


class ContextCompressor:
    """Orchestrates the 5-level compression pipeline.

    Strategies are applied in order of increasing cost.
    Each strategy receives the output of the previous one.
    """

    def __init__(
        self,
        budget_tokens: int = 180_000,
        enable_snip: bool = True,
        enable_collapse: bool = True,
    ) -> None:
        self.budget_tokens = budget_tokens
        self.collapse: ContextCollapseStrategy | None = None
        self.strategies: list[CompressionStrategy] = [
            BudgetReductionStrategy(),
        ]
        if enable_snip:
            self.strategies.append(SnipStrategy())
        self.strategies.append(MicrocompactStrategy())
        if enable_collapse:
            self.collapse = ContextCollapseStrategy()
            self.strategies.append(self.collapse)
        self.auto_compact = AutoCompactStrategy()
        self.strategies.append(self.auto_compact)

    def compress(self, messages: list[Message]) -> CompressionResult:
        """Run compression pipeline until budget is met or strategies exhausted."""
        current = messages[:]
        total_tokens = sum(estimate_tokens(message_to_text(m)) for m in current)

        if total_tokens <= self.budget_tokens:
            return CompressionResult(messages=current)

        total_saved = 0
        for strategy in self.strategies:
            result = strategy.apply(current, self.budget_tokens)
            current = result.messages
            total_saved += result.tokens_saved
            total_tokens = sum(estimate_tokens(message_to_text(m)) for m in current)
            if total_tokens <= self.budget_tokens:
                return CompressionResult(
                    messages=current,
                    compressed=True,
                    strategy_name=result.strategy_name,
                    tokens_saved=total_saved,
                )

        return CompressionResult(
            messages=current,
            compressed=True,
            strategy_name="all_strategies",
            tokens_saved=total_saved,
        )

    def stage_collapse(self, messages: list[Message]) -> None:
        """Stage reduction candidates for zero-cost drain on 413."""
        if self.collapse:
            self.collapse.stage_candidates(messages)

    def drain_collapse(self, messages: list[Message]) -> CompressionResult:
        """Drain staged collapses (zero API cost)."""
        if self.collapse:
            return self.collapse.apply(messages, self.budget_tokens)
        return CompressionResult(messages=messages)

    def force_auto_compact(self, messages: list[Message]) -> CompressionResult:
        """Force last-resort local compaction after prompt-too-long errors."""
        return self.auto_compact.apply(messages, self.budget_tokens)
