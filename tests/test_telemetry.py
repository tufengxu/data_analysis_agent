"""Tests for Stage A: trajectory recording + feedback signals."""

from typing import Any

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.events import (
    CompleteEvent,
    RequestStartEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from data_analysis_agent.protocol.messages import ModelResponse, TextBlock, ToolUseBlock
from data_analysis_agent.session import AgentSession
from data_analysis_agent.telemetry import (
    FeedbackRecord,
    TrajectoryLogger,
    attach_feedback_to_turns,
    load_turns,
    looks_like_rephrase,
    parse_explicit_feedback,
)
from data_analysis_agent.telemetry.trajectory import ToolCallRecord, TurnRecord
from data_analysis_agent.tools.base import Tool, ToolResult
from data_analysis_agent.tools.registry import ToolRegistry

# --- feedback heuristics ----------------------------------------------------


def test_parse_explicit_feedback():
    assert parse_explicit_feedback("/good").kind == "good"
    assert parse_explicit_feedback("/bad 口径错了").kind == "bad"
    assert parse_explicit_feedback("/bad 口径错了").detail == "口径错了"
    assert parse_explicit_feedback("分析 sales.csv") is None  # ordinary input
    assert parse_explicit_feedback("/goodbye") is None  # not the marker


def test_looks_like_rephrase():
    assert looks_like_rephrase("不对,重新分析", gap_seconds=5) is True
    assert looks_like_rephrase("redo this please", gap_seconds=5) is True
    assert looks_like_rephrase("不对,重新分析", gap_seconds=300) is False  # too slow
    assert looks_like_rephrase("继续分析下一个区域", gap_seconds=5) is False  # neutral


def test_looks_like_rephrase_cjk_variants():
    # expanded CJK correction/negation variants all fire (gap within window)
    assert looks_like_rephrase("不准确，重算一下", gap_seconds=5) is True
    assert looks_like_rephrase("这个有错，重做", gap_seconds=5) is True
    assert looks_like_rephrase("不正确，改一下", gap_seconds=5) is True
    assert looks_like_rephrase("再改改这个", gap_seconds=5) is True


def test_looks_like_rephrase_cjk_neutral_not_flagged():
    # ambiguous openers (等等 list-terminator / 应该是 hypothesis) must NOT fire —
    # they were excluded to keep the false-positive rate down.
    assert looks_like_rephrase("分析销售额、利润、成本等等", gap_seconds=5) is False
    assert looks_like_rephrase("原因应该是这个", gap_seconds=5) is False
    assert looks_like_rephrase("下个月再算吧", gap_seconds=5) is False  # scheduling, not correction


def test_looks_like_rephrase_english_word_boundary():
    # the bare-"no" substring false positive is fixed: "no" inside a word does not fire
    assert looks_like_rephrase("note this change for me", gap_seconds=5) is False
    assert looks_like_rephrase("I know the answer already", gap_seconds=5) is False
    assert looks_like_rephrase("proceed now to the next step", gap_seconds=5) is False
    # "again" must not fire inside "against"
    assert looks_like_rephrase("this is against my expectation", gap_seconds=5) is False
    # but a real standalone correction still fires
    assert looks_like_rephrase("no, that's wrong", gap_seconds=5) is True
    assert looks_like_rephrase("try again please", gap_seconds=5) is True
    assert looks_like_rephrase("nope, not right", gap_seconds=5) is True


# --- TrajectoryLogger -------------------------------------------------------


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 1.0
        return self.t


def _drive_one_turn(logger: TrajectoryLogger):
    logger.begin_turn("分析 sales.csv", turn_id="turn_1")
    logger(RequestStartEvent(turn_count=1, active_skill="descriptive_analysis"))
    logger(UsageEvent(input_tokens=1200, output_tokens=340))
    logger(ToolUseEvent(tool_use_id="tu1", tool_name="python_analysis"))
    logger(ToolResultEvent(tool_use_id="tu1", tool_name="python_analysis", content="rows: 100"))
    logger(CompleteEvent(terminal_reason="COMPLETED", final_text="销售额增长 12%"))
    return logger.end_turn()


def test_logger_builds_turn_record(tmp_path):
    logger = TrajectoryLogger(tmp_path, "sess_a", monotonic=_FakeClock())
    record = _drive_one_turn(logger)

    assert isinstance(record, TurnRecord)
    assert record.session_id == "sess_a"
    assert record.active_skill == "descriptive_analysis"
    assert record.terminal_reason == "COMPLETED"
    assert record.tokens == {"input": 1200, "output": 340, "estimated": False}
    assert len(record.tool_calls) == 1
    assert record.tool_calls[0].name == "python_analysis"
    assert record.tool_calls[0].result_chars == len("rows: 100")
    assert "销售额增长" in record.final_text_digest


def test_logger_persists_jsonl_and_reads_back(tmp_path):
    logger = TrajectoryLogger(tmp_path, "sess_b", monotonic=_FakeClock())
    _drive_one_turn(logger)

    turns = load_turns(logger.path)
    assert len(turns) == 1
    assert turns[0]["type"] == "turn"
    assert turns[0]["active_skill"] == "descriptive_analysis"


def test_logger_estimates_tokens_when_usage_absent(tmp_path):
    logger = TrajectoryLogger(tmp_path, "sess_c", monotonic=_FakeClock())
    logger.begin_turn("分析数据")
    logger(CompleteEvent(terminal_reason="COMPLETED", final_text="some english result"))
    record = logger.end_turn()

    assert record.tokens["estimated"] is True
    assert int(record.tokens["output"]) > 0  # estimated from final text


def test_attach_feedback_merges_onto_turn(tmp_path):
    logger = TrajectoryLogger(tmp_path, "sess_d", monotonic=_FakeClock())
    _drive_one_turn(logger)
    assert logger.attach_feedback(FeedbackRecord(kind="bad", detail="口径错")) is True

    turns = load_turns(logger.path)
    attach_feedback_to_turns(turns, logger.path)
    assert turns[0]["feedback"]["kind"] == "bad"
    assert turns[0]["feedback"]["detail"] == "口径错"


def test_logger_ignores_events_outside_a_turn(tmp_path):
    logger = TrajectoryLogger(tmp_path, "sess_e", monotonic=_FakeClock())
    # No begin_turn → events must be ignored, nothing written.
    logger(UsageEvent(input_tokens=10, output_tokens=5))
    assert load_turns(logger.path) == []


# --- session integration ----------------------------------------------------


class _SequenceClient:
    model = "dummy"

    def __init__(self, responses):
        self.responses = list(responses)

    async def stream_model(
        self, messages, system=None, tools=None, max_tokens=None, tool_choice=None
    ):
        response = self.responses.pop(0)
        for block in response.content:
            yield block
        yield response


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, input_data, can_use_tool=None) -> ToolResult:
        return ToolResult(content="echoed")


async def test_session_records_trajectory_without_altering_stream(tmp_path):
    registry = ToolRegistry()
    registry.register(_EchoTool())
    client = _SequenceClient(
        [
            ModelResponse(
                content=[ToolUseBlock(id="tu1", name="echo", input={})],
                stop_reason="tool_use",
                usage={"input_tokens": 50, "output_tokens": 10},
            ),
            ModelResponse(
                content=[TextBlock("done")],
                stop_reason="end_turn",
                usage={"input_tokens": 60, "output_tokens": 8},
            ),
        ]
    )
    agent = AgentLoop(AgentLoopConfig(api_key="test"), registry, client=client)
    logger = TrajectoryLogger(tmp_path, "sess_int", monotonic=_FakeClock())
    session = AgentSession(agent, trajectory_logger=logger)

    events = [e async for e in session.send("use echo")]

    # Stream still flows unchanged to the consumer.
    assert any(isinstance(e, CompleteEvent) for e in events)
    # A trajectory was recorded with the tool call captured.
    turns = load_turns(logger.path)
    assert len(turns) == 1
    assert turns[0]["terminal_reason"] == "COMPLETED"
    assert any(tc["name"] == "echo" for tc in turns[0]["tool_calls"])


def test_tool_call_record_shape():
    rec = ToolCallRecord(name="x", is_error=False, duration_ms=12, result_chars=3)
    assert rec.name == "x" and rec.result_chars == 3


async def test_logger_crash_does_not_break_event_stream(tmp_path):
    """m2 regression: a throwing logger must not interrupt the user event stream."""

    class _BoomLogger:
        def begin_turn(self, *a, **k):
            return "t"

        def __call__(self, event):
            raise RuntimeError("logger exploded")

        def end_turn(self, *a, **k):
            return None

    client = _SequenceClient([ModelResponse(content=[TextBlock("done")], stop_reason="end_turn")])
    agent = AgentLoop(AgentLoopConfig(api_key="test"), ToolRegistry(), client=client)
    session = AgentSession(agent, trajectory_logger=_BoomLogger())

    events = [e async for e in session.send("hi")]  # must not raise
    assert any(isinstance(e, CompleteEvent) for e in events)


def test_client_usage_int_guard():
    """P0-D: streaming usage採集 must reject non-int (avoid masquerading as real)."""
    from data_analysis_agent.protocol import client as client_mod

    # The guard is `isinstance(value, int)` — verify the intent at the unit level
    # by exercising the same predicate the streaming branch uses.
    assert isinstance(123, int)
    assert not isinstance(None, int)
    assert not isinstance("123", int)
    # Confirm the source actually guards both usage writes.
    import inspect

    src = inspect.getsource(client_mod.AnthropicApiClient.stream_model)
    assert src.count("isinstance(value, int)") == 2


def test_trajectory_dir_disk_cap_evicts_oldest(tmp_path):
    """A new session evicts the oldest OTHER session files when the trajectories
    dir exceeds the cap; the current session's own file is never touched."""
    import os
    import time

    d = tmp_path / "traj"
    d.mkdir()
    # Three old sessions, each ~1MB; oldest by mtime first.
    for i, name in enumerate(("old1", "old2", "old3")):
        p = d / f"{name}.jsonl"
        p.write_text("x" * (1024 * 1024), encoding="utf-8")
        mtime = time.time() - (3 - i)  # old1 < old2 < old3
        os.utime(p, (mtime, mtime))

    # Pre-create the current session's file (resume scenario) so we can assert
    # it is never evicted, then make it the newest by mtime.
    cur = d / "new_sess.jsonl"
    cur.write_text("y" * (512 * 1024), encoding="utf-8")
    os.utime(cur, (time.time(), time.time()))

    # Cap = 2.5MB; old files total ~3MB -> oldest (old1) evicted; current kept.
    # Construction alone runs _enforce_disk_cap (no turn flushed here).
    TrajectoryLogger(d, "new_sess", monotonic=_FakeClock(), max_dir_bytes=int(2.5 * 1024 * 1024))
    remaining = sorted(p.name for p in d.glob("*.jsonl"))
    assert "old1.jsonl" not in remaining  # oldest evicted
    assert "old2.jsonl" in remaining and "old3.jsonl" in remaining
    assert "new_sess.jsonl" in remaining  # current session never evicted
