"""CLI plumbing tests: --path must reach the run functions as analysis_paths.

The composition root (AgentRuntime.from_config) is verified in test_runtime to
forward analysis_paths into both data-read tools; here we verify the CLI surface
parses repeatable --path flags and forwards them.
"""

import sys
from dataclasses import replace

import pytest

import data_analysis_agent.__main__ as cli
from data_analysis_agent.config import AgentConfig


def test_main_forwards_repeatable_path_to_run_single(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_run_single(query, config, persist_path, analysis_paths=None, project=None):
        captured["query"] = query
        captured["analysis_paths"] = analysis_paths

    monkeypatch.setattr(cli, "run_single", fake_run_single)
    monkeypatch.setattr(
        cli.AgentConfig, "from_env", classmethod(lambda cls: replace(AgentConfig(), api_key="x"))
    )
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setattr(sys, "argv", ["data-agent", "analyze", "--path", str(a), "--path", str(b)])

    cli.main()

    assert captured["analysis_paths"] == [str(a), str(b)]


def test_main_without_path_passes_none(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_run_single(query, config, persist_path, analysis_paths=None, project=None):
        captured["analysis_paths"] = analysis_paths

    monkeypatch.setattr(cli, "run_single", fake_run_single)
    monkeypatch.setattr(
        cli.AgentConfig, "from_env", classmethod(lambda cls: replace(AgentConfig(), api_key="x"))
    )
    monkeypatch.setattr(sys, "argv", ["data-agent", "analyze"])

    cli.main()

    # None (not []) so the tools fall back to their cwd default.
    assert captured["analysis_paths"] is None


async def test_run_single_records_error_manifest_when_run_turn_crashes(monkeypatch, tmp_path):
    """A crash in run_turn still writes a RunManifest (terminal_reason=error)
    before re-raising — otherwise the failed run leaves no trace in history
    (backlog Wave 1 Slice 1 minor)."""

    class _FakeRuntime:
        async def shutdown(self):
            pass

    monkeypatch.setattr(cli, "build_runtime", lambda *a, **k: _FakeRuntime())

    async def _boom(*a, **k):
        raise RuntimeError("kernel exploded")

    monkeypatch.setattr(cli, "run_turn", _boom)

    recorded: list[tuple[tuple, dict]] = []

    def _capture(*args, **kwargs):
        recorded.append((args, kwargs))
        return tmp_path / "manifest.json"

    monkeypatch.setattr(cli, "_record_run", _capture)

    with pytest.raises(RuntimeError, match="kernel exploded"):
        await cli.run_single("q", object(), None, None, None)

    # The error manifest was recorded with terminal_reason=error + a crash warning.
    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert args[3]["terminal_reason"] == "error"
    assert kwargs["warnings"] and "crashed" in kwargs["warnings"][0]
