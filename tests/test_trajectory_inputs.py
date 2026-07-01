import json
from dataclasses import replace
from pathlib import Path

from data_analysis_agent.config import AgentConfig
from data_analysis_agent.events import ToolResultEvent, ToolUseEvent
from data_analysis_agent.runtime import AgentRuntime
from data_analysis_agent.telemetry.trajectory import (
    TrajectoryLogger,
    _digest_tool_input,
    _extract_referenced_files,
)


def test_enable_trajectory_inputs_defaults_true():
    assert AgentConfig().enable_trajectory_inputs is True


def test_digest_desensitizes_home_path():
    params = {"path": "/Users/testuser/data/sales.csv", "n": 3}
    out = _digest_tool_input(params, home=__import__("pathlib").Path("/Users/testuser"))
    assert "/Users/testuser" not in out
    assert "<path:sales.csv>" in out
    assert json.loads(out)["n"] == 3  # non-path values preserved


def test_digest_truncates_oversize():
    params = {"code": "x" * 5000}
    out = _digest_tool_input(params, home=__import__("pathlib").Path("/Users/u"), cap=100)
    assert out.endswith("…(truncated)")
    assert len(out) == 100 + len("…(truncated)")


def test_extract_referenced_files_by_suffix():
    params = {"path": "/abs/path/orders.xlsx", "other": "not_a_file"}
    assert _extract_referenced_files(params) == ("orders.xlsx",)


def _logger(tmp_path, **kw):
    return TrajectoryLogger(tmp_path / "traj", "s1", **kw)


def _feed(logger, params):
    logger.begin_turn("q")
    logger(ToolUseEvent(tool_use_id="t1", tool_name="data_profile", parameters=params))
    logger(ToolResultEvent(tool_use_id="t1", tool_name="data_profile", content="ok"))
    return logger.end_turn()


def test_capture_records_input_digest_and_refs(tmp_path):
    logger = _logger(tmp_path, home=Path("/Users/u"))
    rec = _feed(logger, {"path": "/Users/u/data/sales.csv"})
    tc = rec.tool_calls[0]
    assert tc.name == "data_profile"
    assert "<path:sales.csv>" in tc.input_digest
    assert tc.referenced_files == ("sales.csv",)


def test_enable_inputs_false_omits_fields(tmp_path):
    logger = _logger(tmp_path, enable_inputs=False)
    rec = _feed(logger, {"path": "/Users/u/data/sales.csv"})
    tc = rec.tool_calls[0]
    assert tc.input_digest == ""
    assert tc.referenced_files == ()
    # other fields still captured
    assert tc.name == "data_profile" and tc.result_chars == 2


class _FakeClient:
    model = "dummy"


def test_runtime_threads_enable_inputs_false(tmp_path, monkeypatch):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    cfg = replace(
        AgentConfig(),
        api_key="x",
        persistent_kernel=False,
        enable_telemetry=True,
        enable_trajectory_inputs=False,
    )
    rt = AgentRuntime.from_config(cfg, client=_FakeClient())
    assert rt.session.trajectory_logger._enable_inputs is False


def test_runtime_threads_enable_inputs_true_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    cfg = replace(AgentConfig(), api_key="x", persistent_kernel=False, enable_telemetry=True)
    rt = AgentRuntime.from_config(cfg, client=_FakeClient())
    assert rt.session.trajectory_logger._enable_inputs is True
