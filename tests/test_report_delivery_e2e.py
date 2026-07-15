"""End-to-end report-delivery wiring (TR-1 / spec 2026-07-14 §3.7).

Drives the LIVE AgentLoop (sequence client) through html_report(document) and
asserts the QA gate fires at the loop level: a DRAFT document is refused (no
artifact), a READY document is written. This is the contract-level test that
closes the "no test drives the live loop through contract -> QA -> render" gap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.events import CompleteEvent, ToolResultEvent
from data_analysis_agent.protocol.messages import ModelResponse, TextBlock, ToolUseBlock
from data_analysis_agent.reporting.contract import (
    BlockRole,
    ReportBlock,
    ReportContract,
    ReportDocument,
)
from data_analysis_agent.tools.html_report import HtmlReportTool


class _SequenceClient:
    """Minimal mock streaming client: returns a fixed response sequence."""

    model = "dummy"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)

    async def stream_model(
        self,
        messages: Any,
        system: Any = None,
        tools: Any = None,
        max_tokens: Any = None,
        tool_choice: Any = None,
    ):
        response = self.responses.pop(0)
        for block in response.content:
            yield block
        yield response


def _draft_doc() -> dict[str, Any]:
    """No contract + no data_scope -> blocker -> readiness DRAFT."""
    return ReportDocument(
        title="draft report",
        contract=None,
        data_scope=None,
        blocks=(ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),),
    ).to_dict()


def _ready_doc() -> dict[str, Any]:
    """Contract + exec summary + data_scope + sourced recommendation -> READY."""
    return ReportDocument(
        title="ready report",
        contract=ReportContract(question="q", explicit_requirement_refs=("u1",)),
        data_scope="sales.csv,上周,100 行",
        blocks=(
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="GMV 上升"),
            ReportBlock(
                block_id="r",
                role=BlockRole.RECOMMENDATION,
                body="加大渠道 A",
                evidence_refs=("e1",),
            ),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="sales.csv"),
        ),
    ).to_dict()


@pytest.mark.asyncio
async def test_live_loop_qa_gate_refuses_draft_and_writes_ready(tmp_path: Path):
    """The live AgentLoop drives html_report(document); the QA gate refuses a
    DRAFT document (is_error, no artifact) and writes a READY one."""
    tool = HtmlReportTool(artifact_dir=tmp_path)
    client = _SequenceClient(
        [
            ModelResponse(
                content=[
                    ToolUseBlock(
                        id="tu_draft", name="html_report", input={"document": _draft_doc()}
                    )
                ],
                stop_reason="tool_use",
            ),
            ModelResponse(
                content=[
                    ToolUseBlock(
                        id="tu_ready", name="html_report", input={"document": _ready_doc()}
                    )
                ],
                stop_reason="tool_use",
            ),
            ModelResponse(content=[TextBlock("done")], stop_reason="end_turn"),
        ]
    )
    agent = AgentLoop(
        AgentLoopConfig(api_key="test", max_turns=10), _tool_registry(tool), client=client
    )

    events = [event async for event in agent.run("make me a report")]

    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(results) == 2

    # DRAFT refused: the gate returns is_error WITHOUT writing a file (the
    # model can self-correct from the surfaced blockers).
    draft_result = next(r for r in results if r.tool_use_id == "tu_draft")
    assert draft_result.is_error is True
    assert "QA" in draft_result.content or "拒绝" in draft_result.content

    # READY written: not an error, exactly one artifact with a ready badge.
    ready_result = next(r for r in results if r.tool_use_id == "tu_ready")
    assert ready_result.is_error is False
    files = list(tmp_path.glob("*.html"))
    assert len(files) == 1  # only the READY doc wrote a file; DRAFT wrote none
    assert "qa-ready" in files[0].read_text(encoding="utf-8")

    assert any(isinstance(e, CompleteEvent) for e in events)


def _tool_registry(tool: HtmlReportTool):
    from data_analysis_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(tool)
    return registry
