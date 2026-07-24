"""G1-2: make_agent_run_fn drives the REAL agent loop (not a lighter agent).

Closes the audit gap that "eval runs the same agent as CLI" was only wired,
never tested. The run_fn builds a real AgentRuntime.from_config, drives the
loop with a fake streaming client, and captures ToolResult/Complete into EvalRun
(including the python_analysis output that feeds numeric anchors).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_analysis_agent.evolution.evaluator import EvalRun, EvalTask, make_agent_run_fn
from data_analysis_agent.protocol.messages import ModelResponse, TextBlock, ToolUseBlock


class _SeqClient:
    """Fake streaming client yielding a fixed response sequence."""

    model = "dummy"

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)

    async def stream_model(
        self, messages, system=None, tools=None, max_tokens=None, tool_choice=None
    ):
        # One response per stream_model call (the loop calls once per turn).
        response = self.responses.pop(0)
        for block in response.content:
            yield block
        yield response


def test_make_agent_run_fn_runs_real_agent_and_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    seq = _SeqClient(
        [
            ModelResponse(
                content=[
                    ToolUseBlock(id="tu1", name="python_analysis", input={"code": "print(42)"})
                ],
                stop_reason="tool_use",
            ),
            ModelResponse(content=[TextBlock("done: 42")], stop_reason="end_turn"),
        ]
    )
    run_fn = make_agent_run_fn(seq, allowed_paths=[tmp_path])
    run = run_fn(EvalTask(task_id="t1", input="compute the answer"), skill=None)

    assert isinstance(run, EvalRun)
    # The tool actually executed through the real loop + sandbox.
    assert run.tool_call_count >= 1
    assert "python_analysis" in run.tools_used
    assert not run.has_error
    # python_analysis output was captured for numeric anchors (G1-2 + Wave 8 path).
    assert len(run.computed_outputs) >= 1
    assert "42" in run.computed_outputs[0]
    # Complete event's final text reached the EvalRun.
    assert run.final_text == "done: 42"


def test_make_agent_run_fn_eval_config_isolates(tmp_path: Path) -> None:
    """The run_fn uses eval_config_for: no kernel/memory/telemetry, permission cleared."""
    from dataclasses import replace

    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.evolution.evaluator import eval_config_for

    base = replace(AgentConfig(), permission_mode="plan", deny_patterns=["x"])
    ec = eval_config_for(base)
    assert ec.persistent_kernel is False
    assert ec.enable_memory is False
    assert ec.enable_telemetry is False
    assert ec.permission_mode == "default"
    assert ec.deny_patterns == []  # cleared: a plan-mode/deny env can't block eval tools
