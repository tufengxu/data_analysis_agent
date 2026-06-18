"""Tests for the compression-layer fixes (pairing safety, CJK tokens, summaries)."""

from data_analysis_agent.agent_loop import ensure_tool_ledger_closed
from data_analysis_agent.context.compression import (
    AutoCompactStrategy,
    BudgetReductionStrategy,
    ContextCollapseStrategy,
    SnipStrategy,
    estimate_tokens,
)
from data_analysis_agent.state_machine import Message


def _tool_use_msg(tool_use_id: str) -> Message:
    return Message(
        role="assistant",
        content=[{"type": "tool_use", "id": tool_use_id, "name": "t", "input": {}}],
    )


def _tool_result_msg(tool_use_id: str, content: str = "ok") -> Message:
    return Message(
        role="user",
        content=[{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
    )


def _window_has_orphan_result(messages: list[Message]) -> bool:
    use_ids = set()
    for msg in messages:
        if isinstance(msg.content, list):
            for block in msg.content:
                if block.get("type") == "tool_use":
                    use_ids.add(block.get("id"))
    for msg in messages:
        if isinstance(msg.content, list):
            for block in msg.content:
                if block.get("type") == "tool_result" and block.get("tool_use_id") not in use_ids:
                    return True
    return False


# --- estimate_tokens -------------------------------------------------------


def test_estimate_tokens_weights_cjk_higher():
    assert estimate_tokens("a" * 100) == 25
    assert estimate_tokens("中" * 100) == 100
    assert estimate_tokens("ab" + "中" * 10) == int(2 * 0.25 + 10)


# --- BudgetReduction -------------------------------------------------------


def test_budget_reduction_truncates_blocks_individually():
    strategy = BudgetReductionStrategy(max_chars=2_000)
    msg = Message(
        role="user",
        content=[
            {"type": "text", "text": "A" * 5_000},
            {"type": "tool_result", "tool_use_id": "tu", "content": "B" * 5_000},
        ],
    )
    result = strategy.apply([msg], budget=10)

    blocks = result.messages[0].content
    assert blocks[0]["text"].startswith("A")
    assert blocks[1]["content"].startswith("B")  # no whole-message duplication
    assert "truncated from 5000 chars" in blocks[0]["text"]
    assert "truncated from 5000 chars" in blocks[1]["content"]
    assert blocks[1]["tool_use_id"] == "tu"  # pairing key preserved


# --- Snip ------------------------------------------------------------------


def test_snip_never_orphans_tool_results():
    messages = [
        Message(role="user", content="old question"),
        _tool_use_msg("tu_1"),
        _tool_result_msg("tu_1"),
        Message(role="user", content="newer question"),
    ]
    # Naive cut at -2 would start the window on the tool_result message.
    result = SnipStrategy(max_messages=2).apply(messages, budget=0)

    assert result.compressed is True
    assert not _window_has_orphan_result(result.messages)
    # The cut walked back to include the owning assistant tool_use.
    assert result.messages[0].role == "assistant"


def test_snip_noop_under_limit():
    messages = [Message(role="user", content="hi")]
    result = SnipStrategy(max_messages=40).apply(messages, budget=0)
    assert result.messages == messages
    assert result.compressed is False


# --- Collapse --------------------------------------------------------------


def test_collapse_prefers_heavy_tool_results_and_preserves_pairing():
    messages = [
        Message(role="user", content="question"),
        Message(role="assistant", content="short text"),
        _tool_result_msg("tu_big", content="X" * 10_000),
        Message(role="assistant", content="conclusion"),
        Message(role="user", content="follow-up"),
        Message(role="assistant", content="tail"),
    ]
    strategy = ContextCollapseStrategy()
    strategy.stage_candidates(messages)
    result = strategy.apply(messages, budget=0)

    collapsed = result.messages[2]
    assert isinstance(collapsed.content, list)
    assert collapsed.content[0]["type"] == "tool_result"
    assert collapsed.content[0]["tool_use_id"] == "tu_big"
    assert collapsed.content[0]["content"] == "[Earlier tool result collapsed]"


def test_collapse_never_stages_assistant_tool_use():
    messages = [
        Message(role="user", content="q"),
        _tool_use_msg("tu_1"),
        _tool_result_msg("tu_1"),
        Message(role="assistant", content="a"),
        Message(role="user", content="next"),
        Message(role="assistant", content="tail"),
    ]
    strategy = ContextCollapseStrategy()
    strategy.stage_candidates(messages)
    assert 1 not in strategy.staged_indices  # the tool_use message


def test_collapse_staging_replaces_previous_staging():
    strategy = ContextCollapseStrategy()
    heavy_at_4 = (
        [Message(role="user", content="s")] * 4
        + [Message(role="user", content="X" * 9_000)]
        + [Message(role="user", content="s")] * 3
    )
    strategy.stage_candidates(heavy_at_4)
    assert strategy.staged_indices == {4}

    short = [Message(role="user", content=f"m{i}") for i in range(5)]
    strategy.stage_candidates(short)
    assert 4 not in strategy.staged_indices  # stale index from the prior turn dropped
    assert all(idx < 3 for idx in strategy.staged_indices)


# --- AutoCompact -----------------------------------------------------------


def test_auto_compact_uses_summary_when_provided():
    messages = [Message(role="user", content=f"msg {i}") for i in range(6)]
    result = AutoCompactStrategy().apply(messages, budget=0, summary="关键结论:A>B")

    summary_msg = result.messages[1]
    assert summary_msg.is_meta is True
    assert "关键结论:A>B" in summary_msg.content
    assert "对话历史摘要" in summary_msg.content


def test_auto_compact_falls_back_to_marker_without_summary():
    messages = [Message(role="user", content=f"msg {i}") for i in range(6)]
    result = AutoCompactStrategy().apply(messages, budget=0)
    assert "summarized" in result.messages[1].content


def test_auto_compact_tail_never_starts_on_orphan_result():
    messages = [
        Message(role="user", content="q"),
        Message(role="assistant", content="filler"),
        _tool_use_msg("tu_9"),
        _tool_result_msg("tu_9"),
        Message(role="assistant", content="answer"),
    ]
    result = AutoCompactStrategy().apply(messages, budget=0)
    assert not _window_has_orphan_result(result.messages)


def test_auto_compact_preview_matches_apply():
    messages = [Message(role="user", content=f"msg {i}") for i in range(6)]
    strategy = AutoCompactStrategy()
    removed = strategy.preview_removed(messages)
    result = strategy.apply(messages, budget=0)
    # kept = first + summary + tail; removed accounts for the rest
    assert len(removed) + len(result.messages) - 1 == len(messages)


# --- Ledger closure --------------------------------------------------------


def test_ledger_closed_positionally_not_appended():
    messages = [
        _tool_use_msg("tu_a"),
        Message(role="user", content="unrelated next question"),
    ]
    closed = ensure_tool_ledger_closed(messages)

    assert len(closed) == 3
    assert closed[1].content[0]["tool_use_id"] == "tu_a"  # inserted in place
    assert closed[2].content == "unrelated next question"


def test_ledger_merges_partial_coverage():
    messages = [
        Message(
            role="assistant",
            content=[
                {"type": "tool_use", "id": "tu_1", "name": "t", "input": {}},
                {"type": "tool_use", "id": "tu_2", "name": "t", "input": {}},
            ],
        ),
        _tool_result_msg("tu_1"),
    ]
    closed = ensure_tool_ledger_closed(messages)

    assert len(closed) == 2
    ids = {block["tool_use_id"] for block in closed[1].content}
    assert ids == {"tu_1", "tu_2"}


def test_ledger_noop_when_closed():
    messages = [_tool_use_msg("tu_1"), _tool_result_msg("tu_1")]
    assert ensure_tool_ledger_closed(messages) == messages


def test_snip_walks_forward_when_backward_degenerates():
    """M2 regression: snip must still bite when the cut sits in a result run."""
    messages = [
        _tool_use_msg("tu_a"),
        _tool_result_msg("tu_a"),
        _tool_result_msg("tu_b"),
        Message(role="user", content="latest question"),
    ]
    result = SnipStrategy(max_messages=2).apply(messages, budget=0)

    assert result.compressed is True
    assert len(result.messages) < len(messages)
    assert not _window_has_orphan_result(result.messages)


def test_auto_compact_folds_leading_orphan_result_into_summary():
    """m4 regression: a malformed history starting on a tool_result carrier."""
    messages = [
        _tool_result_msg("tu_lost"),
        Message(role="user", content="q"),
        Message(role="assistant", content="a"),
        Message(role="user", content="q2"),
        Message(role="assistant", content="a2"),
    ]
    result = AutoCompactStrategy().apply(messages, budget=0)
    assert not _window_has_orphan_result(result.messages)


def test_auto_compact_folds_leading_orphan_tool_use_into_summary():
    """R2-M2 regression: a history starting on a tool_use assistant message
    must not keep it verbatim once its tool_result is summarized away."""
    messages = [
        _tool_use_msg("tu_head"),
        _tool_result_msg("tu_head"),
        Message(role="user", content="q2"),
        Message(role="assistant", content="a2"),
        Message(role="user", content="q3"),
        Message(role="assistant", content="a3"),
    ]
    result = AutoCompactStrategy().apply(messages, budget=0)

    kept_use_ids = set()
    kept_result_ids = set()
    for msg in result.messages:
        if isinstance(msg.content, list):
            for block in msg.content:
                if block.get("type") == "tool_use":
                    kept_use_ids.add(block.get("id"))
                elif block.get("type") == "tool_result":
                    kept_result_ids.add(block.get("tool_use_id"))
    assert kept_use_ids <= kept_result_ids  # no orphan tool_use
    assert not _window_has_orphan_result(result.messages)
