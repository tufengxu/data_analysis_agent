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
