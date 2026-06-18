"""Integration tests for the agent loop and subsystems."""

import tempfile
from pathlib import Path
from typing import Any

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.context.compression import ContextCompressor
from data_analysis_agent.events import (
    CompleteEvent,
    ToolResultEvent,
)
from data_analysis_agent.persistence import MessageStore
from data_analysis_agent.protocol.messages import ModelResponse, TextBlock, ToolUseBlock
from data_analysis_agent.recovery import RecoveryPolicy
from data_analysis_agent.security.permissions import (
    PermissionBehavior,
    PermissionEngine,
    PermissionRule,
)
from data_analysis_agent.skills.builtin import (
    DescriptiveAnalysisSkill,
    TrendAnalysisSkill,
)
from data_analysis_agent.skills.registry import SkillRegistry
from data_analysis_agent.state_machine import AgentState, Message
from data_analysis_agent.tools import (
    FileReadTool,
    NlQueryTool,
    PythonAnalysisTool,
    ToolRegistry,
    VisualizationTool,
)
from data_analysis_agent.tools.base import Tool, ToolResult


class _DummyClient:
    """Stand-in for AnthropicApiClient in tests that don't hit the network."""

    model = "dummy"


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
                "tool_choice": tool_choice,
            }
        )
        response = self.responses.pop(0)
        for block in response.content:
            yield block
        yield response


class _RecordingTool(Tool):
    """Tool used to verify permission decisions happen before execution."""

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


def test_context_compression():
    """Test context compression reduces message size."""
    from data_analysis_agent.context.compression import BudgetReductionStrategy

    strategy = BudgetReductionStrategy(max_chars=100)
    messages = [Message(role="user", content="a" * 200)]
    result = strategy.apply(messages, 1000)

    assert result.compressed is True
    assert result.tokens_saved > 0
    assert len(result.messages) == 1
    assert "truncated" in result.messages[0].content


def test_context_compressor_pipeline():
    """Test full compression pipeline."""
    compressor = ContextCompressor(budget_tokens=100)
    messages = [
        Message(role="user", content="msg1"),
        Message(role="assistant", content="msg2"),
        Message(role="user", content="msg3"),
    ]
    result = compressor.compress(messages)
    assert len(result.messages) >= 1


def test_skill_registry():
    """Test skill registration and matching."""
    registry = SkillRegistry()
    skill = DescriptiveAnalysisSkill()
    registry.register(skill)

    assert registry.get("descriptive_analysis") is skill
    assert registry.match_best("descriptive statistics") is skill
    assert registry.match_best("xyz unrelated") is None


def test_skill_registry_keyword_search():
    """Test skill keyword search."""
    registry = SkillRegistry()
    registry.register(DescriptiveAnalysisSkill())
    registry.register(TrendAnalysisSkill())

    results = registry.find_by_keyword("trend")
    assert len(results) == 1
    assert results[0].name == "trend_analysis"


def test_skill_registry_matches_chinese_analysis_terms():
    """Skill matching should work for common Chinese data-analysis requests."""
    registry = SkillRegistry()
    descriptive = DescriptiveAnalysisSkill()
    trend = TrendAnalysisSkill()
    registry.register(descriptive)
    registry.register(trend)

    assert registry.match_best("请对这个数据集做描述性统计") is descriptive
    assert registry.match_best("帮我分析销售额的趋势变化") is trend


async def test_agent_loop_restricts_tools_to_active_skill_allowed_tools():
    """Matching a skill should prioritize that skill and expose only its tools."""
    registry = ToolRegistry()
    registry.register(FileReadTool())
    registry.register(PythonAnalysisTool())
    registry.register(NlQueryTool())
    registry.register(VisualizationTool())

    skills = SkillRegistry()
    skills.register(DescriptiveAnalysisSkill())

    client = _SequenceClient(
        [ModelResponse(content=[TextBlock("summary")], stop_reason="end_turn")]
    )
    agent = AgentLoop(
        AgentLoopConfig(api_key="test"),
        registry,
        skill_registry=skills,
        client=client,
    )

    events = [event async for event in agent.run("descriptive statistics for sales.csv")]

    assert any(isinstance(event, CompleteEvent) for event in events)
    assert "Active Skill: descriptive_analysis" in client.calls[0]["system"]
    assert [tool["name"] for tool in client.calls[0]["tools"]] == [
        "python_analysis",
        "read_file",
    ]


async def test_agent_loop_permission_engine_denies_tool_before_execution():
    """PermissionEngine deny rules should block tool execution fail-closed."""
    tool = _RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)

    engine = PermissionEngine()
    engine.add_rule(PermissionRule("recording_tool", PermissionBehavior.DENY))

    client = _SequenceClient(
        [
            ModelResponse(
                content=[ToolUseBlock(id="tu_001", name="recording_tool", input={})],
                stop_reason="tool_use",
            ),
            ModelResponse(content=[TextBlock("done")], stop_reason="end_turn"),
        ]
    )
    agent = AgentLoop(
        AgentLoopConfig(api_key="test"),
        registry,
        permission_engine=engine,
        client=client,
    )

    events = [event async for event in agent.run("use the tool")]
    result_events = [event for event in events if isinstance(event, ToolResultEvent)]

    assert tool.called is False
    assert len(result_events) == 1
    assert result_events[0].tool_name == "recording_tool"
    assert result_events[0].is_error is True
    assert "Permission denied" in result_events[0].content


async def test_agent_loop_max_tokens_recovery_injects_continuation_message():
    """max_tokens recovery should change the next request, not just loop unchanged."""
    client = _SequenceClient(
        [
            ModelResponse(content=[TextBlock("partial")], stop_reason="max_tokens"),
            ModelResponse(content=[TextBlock("done")], stop_reason="end_turn"),
        ]
    )
    agent = AgentLoop(
        AgentLoopConfig(api_key="test", max_tokens=128),
        ToolRegistry(),
        client=client,
    )

    events = [event async for event in agent.run("write a long answer")]

    assert any(isinstance(event, CompleteEvent) for event in events)
    assert len(client.calls) == 2
    assert client.calls[1]["max_tokens"] == RecoveryPolicy.RECOVERY_MAX_TOKENS
    assert client.calls[1]["messages"][-1]["role"] == "user"
    assert "continue" in client.calls[1]["messages"][-1]["content"].lower()


def test_message_store_append_and_load():
    """Test JSONL message store."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name

    store = MessageStore(path)
    store.append(Message(role="user", content="hello"))
    store.append(Message(role="assistant", content="hi there"))

    assert len(store) == 2

    messages = store.load_all()
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "hello"

    store.clear()
    assert len(store) == 0

    Path(path).unlink(missing_ok=True)


def test_message_store_fork():
    """Test message store fork."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name

    store = MessageStore(path)
    store.append(Message(role="user", content="msg1"))
    store.append(Message(role="user", content="msg2"))
    store.append(Message(role="user", content="msg3"))

    fork_path = path.replace(".jsonl", "_fork.jsonl")
    forked = store.fork(fork_path, last_n=2)
    assert len(forked) == 2

    store.clear()
    Path(path).unlink(missing_ok=True)
    Path(fork_path).unlink(missing_ok=True)


def test_build_message_store_uses_persist_path(tmp_path):
    """CLI helper should honor --persist by creating a MessageStore."""
    from data_analysis_agent.__main__ import build_message_store

    store = build_message_store(tmp_path / "session.jsonl")

    assert isinstance(store, MessageStore)
    store.append(Message(role="user", content="hello"))
    assert len(store) == 1


def test_visualization_tool_schema():
    """Test VisualizationTool schema."""
    tool = VisualizationTool()
    assert tool.name == "visualization"
    schema = tool.input_schema
    assert "chart_type" in schema["properties"]
    assert "line" in schema["properties"]["chart_type"]["enum"]


def test_visualization_tool_validation():
    """Test VisualizationTool validation."""
    tool = VisualizationTool()
    assert tool.validate_input({"chart_type": "line"}).valid is True
    assert tool.validate_input({"chart_type": "invalid"}).valid is False
    assert tool.validate_input({}).valid is False


async def test_visualization_tool_generate():
    """Test VisualizationTool code generation."""
    tool = VisualizationTool()
    result = await tool.call(
        {"chart_type": "line", "data_source": "data.csv", "x_column": "date", "y_column": "value"}
    )
    assert "Generated line chart code" in result.content
    assert "python_analysis" in result.content
    assert result.metadata["chart_type"] == "line"


async def test_agent_loop_with_mock():
    """Test AgentLoop initialization and structure."""
    config = AgentLoopConfig(
        system_prompt="You are a test agent.",
        max_turns=5,
        max_tokens=1024,
        model="claude-haiku-4-5",
        api_key="fake-key-for-test",
    )
    registry = ToolRegistry()
    registry.register(FileReadTool())
    compressor = ContextCompressor()

    agent = AgentLoop(config, registry, compressor=compressor, client=_DummyClient())
    assert agent.config.max_turns == 5
    assert agent.registry is registry
    assert agent.compressor is compressor


def test_ledger_closure():
    """Test ledger closure finds orphan tool_use blocks."""
    from data_analysis_agent.agent_loop import AgentLoop

    config = AgentLoopConfig(api_key="test")
    agent = AgentLoop(config, ToolRegistry(), client=_DummyClient())

    state = AgentState(
        messages=[
            Message(
                role="assistant",
                content=[{"type": "tool_use", "id": "tu_001", "name": "read_file", "input": {}}],
            )
        ]
    )
    new_state = agent._yield_missing_tool_results(state)
    assert len(new_state.messages) == 2
    assert new_state.messages[1].role == "user"


def test_ledger_closure_no_orphans():
    """Test ledger closure with no orphans."""
    from data_analysis_agent.agent_loop import AgentLoop

    config = AgentLoopConfig(api_key="test")
    agent = AgentLoop(config, ToolRegistry(), client=_DummyClient())

    state = AgentState(
        messages=[
            Message(
                role="assistant",
                content=[{"type": "tool_use", "id": "tu_001", "name": "read_file", "input": {}}],
            ),
            Message(
                role="user",
                content=[{"type": "tool_result", "tool_use_id": "tu_001", "content": "ok"}],
            ),
        ]
    )
    new_state = agent._yield_missing_tool_results(state)
    assert len(new_state.messages) == 2
