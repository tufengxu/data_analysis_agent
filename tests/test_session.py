"""Tests for AgentSession multi-turn behavior and the ASK approval channel."""

import asyncio
from typing import Any

import pytest

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.events import StateChangeEvent, ToolResultEvent
from data_analysis_agent.persistence import MessageStore
from data_analysis_agent.protocol.messages import ModelResponse, TextBlock, ToolUseBlock
from data_analysis_agent.security.permissions import (
    PermissionBehavior,
    PermissionEngine,
    PermissionRule,
)
from data_analysis_agent.session import AgentSession
from data_analysis_agent.skills.builtin import (
    CorrelationAnalysisSkill,
    DescriptiveAnalysisSkill,
)
from data_analysis_agent.skills.registry import SkillRegistry
from data_analysis_agent.state_machine import Message
from data_analysis_agent.tools.base import Tool, ToolResult
from data_analysis_agent.tools.registry import ToolRegistry


class _SequenceClient:
    """Mock streaming client that returns a fixed response sequence."""

    model = "dummy"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def stream_model(
        self, messages, system=None, tools=None, max_tokens=None, tool_choice=None
    ):
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "tools": tools or [],
                "max_tokens": max_tokens,
            }
        )
        response = self.responses.pop(0)
        for block in response.content:
            yield block
        yield response


class _RecordingTool(Tool):
    def __init__(self):
        self.called = False

    @property
    def name(self) -> str:
        return "recording_tool"

    @property
    def description(self) -> str:
        return "Records whether it was called."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, input_data: dict[str, Any], can_use_tool=None) -> ToolResult:
        self.called = True
        return ToolResult(content="called")


def _end_turn(text: str) -> ModelResponse:
    return ModelResponse(content=[TextBlock(text)], stop_reason="end_turn")


async def _drain(aiter):
    return [event async for event in aiter]


async def test_send_carries_history_between_turns():
    client = _SequenceClient([_end_turn("第一轮结论"), _end_turn("第二轮结论")])
    agent = AgentLoop(AgentLoopConfig(api_key="test"), ToolRegistry(), client=client)
    session = AgentSession(agent)

    await _drain(session.send("分析 sales.csv"))
    await _drain(session.send("刚才那个异常拆开看"))

    second_call_messages = client.calls[1]["messages"]
    assert len(second_call_messages) == 3  # turn-1 user + assistant + turn-2 user
    assert second_call_messages[0]["content"] == "分析 sales.csv"
    assert any(
        block.get("text") == "第一轮结论"
        for block in second_call_messages[1]["content"]
        if isinstance(block, dict)
    )
    assert second_call_messages[2]["content"] == "刚才那个异常拆开看"


async def test_send_persists_only_new_messages(tmp_path):
    store = MessageStore(tmp_path / "session.jsonl")
    client = _SequenceClient([_end_turn("a"), _end_turn("b")])
    agent = AgentLoop(AgentLoopConfig(api_key="test"), ToolRegistry(), store=store, client=client)
    session = AgentSession(agent, store)

    await _drain(session.send("q1"))
    await _drain(session.send("q2"))

    # 2 user + 2 assistant; history must not be re-appended on turn 2.
    assert len(store) == 4


async def test_resume_restores_history_and_closes_ledger(tmp_path):
    store = MessageStore(tmp_path / "session.jsonl")
    store.append(Message(role="user", content="hi"))
    store.append(
        Message(
            role="assistant",
            content=[{"type": "tool_use", "id": "tu_1", "name": "x", "input": {}}],
        )
    )

    client = _SequenceClient([_end_turn("resumed")])
    agent = AgentLoop(AgentLoopConfig(api_key="test"), ToolRegistry(), store=store, client=client)
    session = AgentSession.resume(agent, store)

    # Orphan tool_use from the interrupted run is closed positionally.
    assert len(session.history) == 3
    closure = session.history[2]
    assert closure.role == "user"
    assert closure.content[0]["tool_use_id"] == "tu_1"

    await _drain(session.send("continue"))
    sent = client.calls[0]["messages"]
    assert sent[0]["content"] == "hi"
    assert sent[-1]["content"] == "continue"


async def test_skill_routing_follows_latest_user_message():
    skills = SkillRegistry()
    skills.register(DescriptiveAnalysisSkill())
    skills.register(CorrelationAnalysisSkill())

    client = _SequenceClient([_end_turn("一"), _end_turn("二")])
    agent = AgentLoop(
        AgentLoopConfig(api_key="test"),
        ToolRegistry(),
        skill_registry=skills,
        client=client,
    )
    session = AgentSession(agent)

    await _drain(session.send("帮我做描述性统计"))
    await _drain(session.send("现在做相关性分析"))

    assert "descriptive_analysis" in client.calls[0]["system"]
    assert "correlation_analysis" in client.calls[1]["system"]


def _tool_use_response(tool: str) -> ModelResponse:
    return ModelResponse(
        content=[ToolUseBlock(id="tu_ask", name=tool, input={})],
        stop_reason="tool_use",
    )


def _ask_agent(tool: Tool, approval_handler=None) -> tuple[AgentLoop, _SequenceClient]:
    registry = ToolRegistry()
    registry.register(tool)
    engine = PermissionEngine()
    engine.add_rule(PermissionRule(tool.name, PermissionBehavior.ASK))
    client = _SequenceClient([_tool_use_response(tool.name), _end_turn("done")])
    agent = AgentLoop(
        AgentLoopConfig(api_key="test"),
        registry,
        permission_engine=engine,
        approval_handler=approval_handler,
        client=client,
    )
    return agent, client


async def test_ask_permission_approved_executes_tool():
    tool = _RecordingTool()

    async def approve(name: str, params: dict[str, Any]) -> bool:
        return True

    agent, _ = _ask_agent(tool, approve)
    events = await _drain(agent.run("use it"))

    assert tool.called is True
    confirm_states = [e.new_state for e in events if isinstance(e, StateChangeEvent)]
    assert "AWAITING_CONFIRMATION" in confirm_states


async def test_ask_permission_denied_blocks_tool():
    tool = _RecordingTool()

    async def deny(name: str, params: dict[str, Any]) -> bool:
        return False

    agent, _ = _ask_agent(tool, deny)
    events = await _drain(agent.run("use it"))

    assert tool.called is False
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.is_error is True
    assert "declined" in result.content


async def test_ask_without_handler_denies_fail_closed():
    tool = _RecordingTool()
    agent, _ = _ask_agent(tool, None)
    events = await _drain(agent.run("use it"))

    assert tool.called is False
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.is_error is True
    assert "no interactive approval handler" in result.content


async def test_resume_rewrites_store_so_disk_matches_memory(tmp_path):
    """C1 regression: ledger closure at resume must also repair the disk."""
    store = MessageStore(tmp_path / "session.jsonl")
    store.append(Message(role="user", content="hi"))
    store.append(
        Message(
            role="assistant",
            content=[{"type": "tool_use", "id": "tu_1", "name": "x", "input": {}}],
        )
    )

    client = _SequenceClient([_end_turn("resumed")])
    agent = AgentLoop(AgentLoopConfig(api_key="test"), ToolRegistry(), store=store, client=client)
    session = AgentSession.resume(agent, store)

    on_disk = store.load_all()
    assert on_disk == session.history  # disk and memory identical after repair
    assert on_disk[2].content[0]["type"] == "tool_result"

    # A second resume needs no further repair (stable fixpoint).
    again = AgentSession.resume(agent, store)
    assert again.history == on_disk


async def test_send_abandoned_midstream_still_updates_history():
    """C2 regression: breaking out of the event stream must not lose history."""
    client = _SequenceClient([_end_turn("answer")])
    agent = AgentLoop(AgentLoopConfig(api_key="test"), ToolRegistry(), client=client)
    session = AgentSession(agent)

    stream = session.send("q1")
    async for _event in stream:
        break  # consumer bails on the first event (shutdown / Ctrl-C)
    await stream.aclose()

    assert len(session.history) >= 1
    assert session.history[0].content == "q1"


async def test_cancel_mid_tool_persists_ledger_closure(tmp_path):
    """R2-m3 regression: cancellation during a tool run must leave a closed,
    replayable ledger on disk that matches the in-memory history."""
    store = MessageStore(tmp_path / "session.jsonl")
    started = asyncio.Event()
    never = asyncio.Event()

    class _HangTool(Tool):
        @property
        def name(self) -> str:
            return "hang_tool"

        @property
        def description(self) -> str:
            return "hangs until cancelled"

        @property
        def input_schema(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def call(self, input_data, can_use_tool=None) -> ToolResult:
            started.set()
            await never.wait()
            return ToolResult(content="unreachable")

    registry = ToolRegistry()
    registry.register(_HangTool())
    client = _SequenceClient([_tool_use_response("hang_tool")])
    agent = AgentLoop(AgentLoopConfig(api_key="test"), registry, store=store, client=client)
    session = AgentSession(agent, store)

    async def consume():
        async for _event in session.send("go"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    on_disk = store.load_all()
    assert on_disk == session.history  # disk and memory identical
    closure = on_disk[-1]
    assert closure.role == "user"
    assert closure.content[0]["type"] == "tool_result"
    assert closure.content[0]["tool_use_id"] == "tu_ask"
