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

# Heuristics: English/code ~1 token per 4 chars; CJK and other non-ASCII text
# ~1 token per char. A flat 0.25/char underestimated Chinese sessions ~4x,
# defeating the budget gate and causing avoidable 413s.
TOKENS_PER_ASCII_CHAR = 0.25
TOKENS_PER_OTHER_CHAR = 1.0


def estimate_tokens(text: str) -> int:
    """Rough token count estimate, weighted by character class."""
    ascii_chars = len(text.encode("ascii", "ignore"))
    other_chars = len(text) - ascii_chars
    return int(ascii_chars * TOKENS_PER_ASCII_CHAR + other_chars * TOKENS_PER_OTHER_CHAR)


def _is_tool_result_message(msg: Message) -> bool:
    """True if the message carries tool_result blocks (pairing-sensitive)."""
    return (
        msg.role == "user"
        and isinstance(msg.content, list)
        and any(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in msg.content
        )
    )


def _has_tool_use(msg: Message) -> bool:
    """True if the message carries tool_use blocks (pairing-sensitive)."""
    return isinstance(msg.content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_use" for block in msg.content
    )


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

    # Floor so a heavily-split message still keeps a useful preview per block.
    # Kept small: with n blocks the post-cap message size is bounded by
    # max(max_chars, n * MIN_BLOCK_CHARS), so a large floor would let
    # many-block messages blow well past the per-message budget.
    MIN_BLOCK_CHARS = 200

    def apply(self, messages: list[Message], budget: int) -> CompressionResult:
        compressed = False
        tokens_saved = 0
        result: list[Message] = []

        for msg in messages:
            text = message_to_text(msg)
            if len(text) <= self.max_chars:
                result.append(msg)
                continue

            if isinstance(msg.content, str):
                truncated = text[: self.max_chars]
                tokens_saved += estimate_tokens(text[self.max_chars :])
                new_content = truncated + f"\n... [truncated from {len(text)} chars]"
                result.append(Message(role=msg.role, content=new_content, is_meta=msg.is_meta))
                compressed = True
                continue

            # Structured content: truncate each oversized text / tool_result
            # block individually so blocks are never replaced by duplicated
            # whole-message text.
            new_blocks, saved = self._truncate_blocks(msg.content)
            tokens_saved += saved
            if saved > 0:
                compressed = True
                result.append(Message(role=msg.role, content=new_blocks, is_meta=msg.is_meta))
            else:
                result.append(msg)

        return CompressionResult(
            messages=result,
            compressed=compressed,
            strategy_name="budget_reduction",
            tokens_saved=tokens_saved,
        )

    def _truncate_blocks(self, blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Cap text-bearing blocks to an even share of the message budget."""

        def _block_text(block: dict[str, Any]) -> str | None:
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                return str(block["text"])
            if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                return str(block["content"])
            return None

        textual = [b for b in blocks if isinstance(b, dict) and _block_text(b) is not None]
        per_block = max(self.MIN_BLOCK_CHARS, self.max_chars // max(1, len(textual)))

        new_blocks: list[dict[str, Any]] = []
        saved = 0
        for block in blocks:
            text = _block_text(block) if isinstance(block, dict) else None
            if text is None or len(text) <= per_block:
                new_blocks.append(block)
                continue
            saved += estimate_tokens(text[per_block:])
            clipped = text[:per_block] + f"\n... [truncated from {len(text)} chars]"
            patched = dict(block)
            if block.get("type") == "text":
                patched["text"] = clipped
            else:
                patched["content"] = clipped
            new_blocks.append(patched)
        return new_blocks, saved


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

        # Pairing safety: a window starting on a tool_result message orphans
        # results whose tool_use was snipped — the API rejects that with a 400.
        # Walk the cut back to the owning assistant message; if that degenerates
        # to "keep everything", walk forward instead (dropping the orphan
        # results keeps the window valid and guarantees the snip still bites).
        cut = len(messages) - self.max_messages
        while cut > 0 and _is_tool_result_message(messages[cut]):
            cut -= 1
        if cut == 0:
            cut = len(messages) - self.max_messages
            while cut < len(messages) and _is_tool_result_message(messages[cut]):
                cut += 1
            if cut >= len(messages):
                return CompressionResult(messages=messages)

        keep = messages[cut:]
        removed = messages[:cut]
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
                    # Meta only if both halves are meta — merged user-visible
                    # content must never be hidden behind the meta flag.
                    is_meta=msg.is_meta and next_msg.is_meta,
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

    # Collapse only the heaviest middle messages instead of all of them, so
    # key analysis conclusions survive a drain.
    COLLAPSE_FRACTION = 0.3

    def __init__(self) -> None:
        self.staged_indices: set[int] = set()

    def stage_candidates(self, messages: list[Message]) -> None:
        """Stage the heaviest middle messages, replacing any prior staging.

        Replacement (not accumulation) keeps indices in sync with the current
        message list. Assistant messages carrying tool_use are skipped:
        collapsing one would orphan the tool_result that follows it.
        Tool-result carriers rank first — raw data echoes are the cheapest
        context to lose.
        """
        self.staged_indices.clear()
        if len(messages) <= 4:
            return
        scored: list[tuple[bool, int, int]] = []
        for idx in range(1, len(messages) - 2):
            msg = messages[idx]
            if _has_tool_use(msg):
                continue
            tokens = estimate_tokens(message_to_text(msg))
            scored.append((not _is_tool_result_message(msg), -tokens, idx))
        if not scored:
            return
        scored.sort()
        keep_n = max(1, int(len(scored) * self.COLLAPSE_FRACTION))
        self.staged_indices = {idx for _, _, idx in scored[:keep_n]}

    def apply(self, messages: list[Message], budget: int) -> CompressionResult:
        # Re-stage on the list we actually receive. Earlier pipeline strategies
        # (Snip removes oldest; Microcompact merges adjacent) mutate the list
        # before we run, so indices staged on the pre-pipeline list can shift
        # onto a different message — including an assistant tool_use message
        # (which stage_candidates deliberately skips): collapsing that to a
        # placeholder would orphan its tool_result and trigger an API 400.
        # stage_candidates is idempotent and clears prior staging, so this is
        # both cheap and always consistent with the list we collapse.
        self.stage_candidates(messages)
        if not self.staged_indices:
            return CompressionResult(messages=messages)

        result: list[Message] = []
        saved = 0
        for idx, msg in enumerate(messages):
            if idx not in self.staged_indices:
                result.append(msg)
                continue
            saved += estimate_tokens(message_to_text(msg))
            if _is_tool_result_message(msg):
                # Preserve tool_use/tool_result pairing: stub each block
                # instead of replacing the message with plain text.
                assert isinstance(msg.content, list)
                stubs: list[dict[str, Any]] = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("tool_use_id", ""),
                        "content": "[Earlier tool result collapsed]",
                    }
                    for block in msg.content
                    if isinstance(block, dict) and block.get("type") == "tool_result"
                ]
                result.append(Message(role="user", content=stubs, is_meta=msg.is_meta))
            else:
                result.append(
                    Message(
                        role=msg.role,
                        content="[Earlier context collapsed]",
                        is_meta=True,
                    )
                )

        self.staged_indices.clear()
        return CompressionResult(
            messages=result,
            compressed=True,
            strategy_name="context_collapse",
            tokens_saved=saved,
        )


class AutoCompactStrategy:
    """L5: Summary-based compaction (last resort).

    The harness may pass a model-generated summary of the removed span via
    ``summary`` (AgentLoop owns the API client and computes it; this module
    must stay free of protocol imports). Without one — or when the summary
    call fails — this degrades to the local placeholder marker.
    """

    KEEP_TAIL = 2

    def _split(self, messages: list[Message]) -> tuple[list[Message], int, list[Message]] | None:
        """Return (head, tail_start, removed) or None when too short to compact."""
        if len(messages) <= self.KEEP_TAIL + 1:
            return None
        # The head is normally the original user request; if a malformed
        # history starts on a tool_result carrier OR a tool_use assistant
        # message, keeping it verbatim would orphan it (its counterpart gets
        # summarized away) — fold it into the removed span instead.
        keep_head = not _is_tool_result_message(messages[0]) and not _has_tool_use(messages[0])
        head = [messages[0]] if keep_head else []
        tail_start = len(messages) - self.KEEP_TAIL
        # Pairing safety: never start the kept tail on an orphan tool_result.
        while tail_start > len(head) and _is_tool_result_message(messages[tail_start]):
            tail_start -= 1
        removed = messages[len(head) : tail_start]
        if not removed:
            return None
        return head, tail_start, removed

    def preview_removed(self, messages: list[Message]) -> list[Message]:
        """Messages that apply() would drop — input for an external summarizer."""
        split = self._split(messages)
        return split[2] if split else []

    def apply(
        self,
        messages: list[Message],
        budget: int,
        summary: str | None = None,
    ) -> CompressionResult:
        split = self._split(messages)
        if split is None:
            return CompressionResult(messages=messages)
        head, tail_start, removed = split
        saved = sum(estimate_tokens(message_to_text(m)) for m in removed)

        if summary:
            content = "[对话历史摘要(自动压缩,细节已省略)]\n" + summary
        else:
            content = f"[Previous {len(removed)} messages summarized]"
        summary_msg = Message(role="user", content=content, is_meta=True)
        result = [*head, summary_msg, *messages[tail_start:]]

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

    def force_auto_compact(
        self, messages: list[Message], summary: str | None = None
    ) -> CompressionResult:
        """Force last-resort compaction after prompt-too-long errors.

        ``summary`` is an optional model-generated digest of the dropped span
        (see AutoCompactStrategy.apply).
        """
        return self.auto_compact.apply(messages, self.budget_tokens, summary=summary)
