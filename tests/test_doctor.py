"""Tests for the doctor health checks (P1-1.7)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from data_analysis_agent.config import AgentConfig
from data_analysis_agent.doctor import check_all

_VALID = {"pass", "warn", "fail", "info"}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "daa_home"
    h.mkdir()
    monkeypatch.setenv("DAA_HOME", str(h))
    return h


def _find(results: list, name: str):
    return next(r for r in results if r.name == name)


def test_check_all_returns_expected_checks(home: Path) -> None:
    results = check_all(AgentConfig(api_key="k"))
    names = [r.name for r in results]
    for expected in (
        "API key",
        "Data extras",
        "DAA_HOME writable",
        "disk · trajectories",
        "disk · total ~/.daa",
        "ECharts",
        "Permission",
        "Kernel python",
        "Web port :8000",
    ):
        assert expected in names, expected
    assert all(r.status in _VALID for r in results)


def test_api_key_pass_when_set(home: Path) -> None:
    assert _find(check_all(AgentConfig(api_key="k")), "API key").status == "pass"


def test_api_key_fail_when_empty(home: Path) -> None:
    assert _find(check_all(AgentConfig(api_key="")), "API key").status == "fail"


def test_daa_home_pass_when_writable(home: Path) -> None:
    assert _find(check_all(AgentConfig(api_key="k")), "DAA_HOME writable").status == "pass"


def test_disk_usage_reflects_files(home: Path) -> None:
    traj = home / "trajectories"
    traj.mkdir()
    (traj / "s.jsonl").write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
    detail = _find(check_all(AgentConfig(api_key="k")), "disk · trajectories").detail
    assert "MiB" in detail
    assert "0.0 MiB" not in detail


def test_check_all_never_raises_on_missing_subdirs(home: Path) -> None:
    """No ~/.daa subdirs exist yet — doctor must report 0.0 MiB, not crash."""
    results = check_all(AgentConfig(api_key="k"))
    assert all(r.status in _VALID for r in results)
    assert _find(results, "disk · total ~/.daa").detail == "0.0 MiB"


def test_cli_doctor_runs(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """`data-agent doctor` is intercepted and prints a report without raising."""
    import data_analysis_agent.__main__ as cli

    monkeypatch.setattr(
        cli.AgentConfig, "from_env", classmethod(lambda cls: AgentConfig(api_key="k"))
    )
    monkeypatch.setattr(sys, "argv", ["data-agent", "doctor"])
    cli.main()  # no FAIL under an isolated writable home with a key → returns normally
