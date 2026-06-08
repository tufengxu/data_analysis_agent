"""Integration test: agent_loop stores originals and injects retrieval markers."""

from __future__ import annotations

from typing import Any

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.protocol.messages import ToolUseBlock
from data_analysis_agent.sampling import SamplingConfig
from data_analysis_agent.sampling.result_store import ResultStore
from data_analysis_agent.state_machine import AgentState, Message
from data_analysis_agent.tools.base import CanUseToolFn, Tool, ToolResult
from data_analysis_agent.tools.registry import ToolRegistry


class _DummyClient:
    """Stand-in for AnthropicApiClient (no network, avoids importing anthropic)."""

    model = "dummy"


class _BigTool(Tool):
    @property
    def name(self) -> str:
        return "big"

    @property
    def description(self) -> str:
        return "emits a large result"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    async def call(
        self, input_data: dict[str, Any], can_use_tool: CanUseToolFn | None = None
    ) -> ToolResult:
        return ToolResult(content="col\n" + "\n".join(f"row{i}" for i in range(2000)))


def _agent(store):
    registry = ToolRegistry()
    registry.register(_BigTool())
    return AgentLoop(
        AgentLoopConfig(api_key="x", model="m"),
        registry,
        result_store=store,
        sampling_config=SamplingConfig(trigger_chars=200),
        client=_DummyClient(),
    )


def test_context_pressure_ratio():
    agent = _agent(None)
    agent.compressor.budget_tokens = 1000
    msgs = [Message(role="user", content="x" * 2000)]  # ~500 tokens
    p = agent._context_pressure(msgs)
    assert 0.0 <= p <= 1.0
    assert p > 0.4


async def test_large_result_stored_and_marked(tmp_path):
    store = ResultStore(tmp_path / "r")
    agent = _agent(store)
    state = AgentState(messages=[Message(role="user", content="hi")])
    blocks = [ToolUseBlock(id="call_1", name="big", input={})]
    results = await agent._execute_tools(blocks, state)
    assert "retrieve_result" in results[0].content
    page = store.get("call_1")
    assert page is not None
    assert page.total_lines == 2001  # full original stored (col + 2000 rows)
    deep = store.get("call_1", query="row1999")  # reachable via query -> full original kept
    assert deep is not None and "row1999" in deep.text


async def test_small_result_not_stored(tmp_path):
    store = ResultStore(tmp_path / "r")
    registry = ToolRegistry()

    class _SmallTool(_BigTool):
        async def call(self, input_data, can_use_tool=None):
            return ToolResult(content="tiny")

    registry.register(_SmallTool())
    agent = AgentLoop(
        AgentLoopConfig(api_key="x", model="m"),
        registry,
        result_store=store,
        sampling_config=SamplingConfig(trigger_chars=200),
        client=_DummyClient(),
    )
    state = AgentState(messages=[Message(role="user", content="hi")])
    results = await agent._execute_tools([ToolUseBlock(id="c2", name="big", input={})], state)
    assert "retrieve_result" not in results[0].content
    assert store.get("c2") is None
