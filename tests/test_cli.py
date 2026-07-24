"""CLI plumbing tests: --path must reach the run functions as analysis_paths.

The composition root (AgentRuntime.from_config) is verified in test_runtime to
forward analysis_paths into both data-read tools; here we verify the CLI surface
parses repeatable --path flags and forwards them.
"""

import sys
from dataclasses import replace

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
