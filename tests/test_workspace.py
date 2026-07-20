"""Tests for the project workspace (P1-2 Slice 1).

Pure workspace lifecycle tests + one composition-root routing test verifying that
``AgentRuntime.from_config(project=...)`` lands session-facing state under the
project root while the no-project path is unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from data_analysis_agent.workspace import (
    Project,
    ProjectManifest,
    RunManifest,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate DAA_HOME so tests never touch the real ~/.daa."""
    h = tmp_path / "daa_home"
    h.mkdir()
    monkeypatch.setenv("DAA_HOME", str(h))
    return h


FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _manifest_project(home: Path, project_id: str = "demo") -> Project:
    return Project.init(
        project_id,
        home=home,
        authorized_paths=["/data/sales.csv"],
        model="claude-sonnet-5",
        preset="local_safe",
        now=lambda: FIXED_NOW,
    )


def test_init_creates_layout_and_manifest(home: Path) -> None:
    proj = _manifest_project(home)

    assert proj.project_id == "demo"
    assert proj.root == home / "projects" / "demo"
    for sub in ("sessions", "artifacts", "results", "workspace", "runs", "uploads", "logs"):
        assert (proj.root / sub).is_dir(), f"missing subdir {sub}"

    manifest = json.loads((proj.root / "project.json").read_text(encoding="utf-8"))
    assert manifest["project_id"] == "demo"
    assert manifest["created_at"] == FIXED_NOW.isoformat()
    assert manifest["authorized_paths"] == ["/data/sales.csv"]
    assert manifest["model"] == "claude-sonnet-5"
    assert manifest["preset"] == "local_safe"
    assert manifest["runs"] == []


def test_init_is_idempotent_does_not_clobber(home: Path) -> None:
    proj = _manifest_project(home)
    # Add a run so we can prove re-init preserves the index + created_at.
    proj.add_run(_run("r1", "demo"))
    assert proj.manifest.runs == ["r1"]

    reinit = Project.init("demo", home=home, now=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert reinit.manifest.created_at == FIXED_NOW.isoformat()  # not overwritten
    assert reinit.manifest.runs == ["r1"]  # index preserved


def test_open_missing_raises(home: Path) -> None:
    with pytest.raises(KeyError):
        Project.open("nope", home=home)


def test_open_path_requires_manifest(tmp_path: Path) -> None:
    empty = tmp_path / "notaproject"
    empty.mkdir()
    with pytest.raises(KeyError):
        Project.open_path(empty)


def test_list_sorts_and_skips_unmanaged(home: Path) -> None:
    Project.init("beta", home=home, now=lambda: FIXED_NOW)
    Project.init("alpha", home=home, now=lambda: FIXED_NOW)
    # A directory under projects/ without a manifest must be skipped, not crash.
    (home / "projects" / "stray").mkdir()

    ids = [p.project_id for p in Project.list_projects(home=home)]
    assert ids == ["alpha", "beta"]


def test_add_run_writes_manifest_and_index(home: Path) -> None:
    proj = _manifest_project(home)
    proj.add_run(_run("r1", "demo"))
    proj.add_run(_run("r2", "demo"))

    run_path = proj.run_manifest_path("r2")
    assert run_path.is_file()
    persisted = RunManifest.from_dict(json.loads(run_path.read_text(encoding="utf-8")))
    assert persisted.run_id == "r2"
    assert persisted.terminal_reason == "max_turns"

    reloaded = Project.open("demo", home=home)
    assert reloaded.manifest.runs == ["r1", "r2"]


def test_history_newest_first_skips_missing(home: Path) -> None:
    proj = _manifest_project(home)
    proj.add_run(_run("r1", "demo"))
    proj.add_run(_run("r2", "demo"))
    # Corrupt one manifest file; history must skip it, not crash.
    proj.run_manifest_path("r1").write_text("{ not json", encoding="utf-8")

    runs = proj.history()
    assert [r.run_id for r in runs] == ["r2"]


def test_project_status_cli_handles_corrupt_manifest(home: Path) -> None:
    """A corrupt project.json must exit cleanly, not dump a traceback (M1 guard)."""
    import data_analysis_agent.__main__ as cli

    proj = _manifest_project(home, "demo")
    proj.manifest_path.write_text("{ broken json", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli._run_project_cli(["status", "demo"])
    assert exc.value.code == 1


def test_manifest_roundtrip_tolerant() -> None:
    payload = {
        "project_id": "x",
        "created_at": "t",
        "root": "/r",
        "authorized_paths": ["a", "b"],
        "runs": ["1", "2"],
        "unknown_future_field": "ignored",
    }
    m = ProjectManifest.from_dict(payload)
    assert m.authorized_paths == ["a", "b"]
    assert m.runs == ["1", "2"]
    # Round-trip drops unknown keys but keeps known ones.
    again = ProjectManifest.from_dict(json.loads(json.dumps(m.to_dict())))
    assert again == m


def _run(run_id: str, project_id: str) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        project_id=project_id,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        request="sum revenue",
        authorized_paths=["/data/sales.csv"],
        session_id="sess-1",
        event_counts={"CompleteEvent": 1, "ToolUseEvent": 2},
        tool_calls={"python_analysis": 2},
        artifacts=["/proj/artifacts/chart.png"],
        terminal_reason="max_turns",
        token_usage={"input_tokens": 100, "output_tokens": 50},
        warnings=[],
    )


def test_from_config_routes_session_state_under_project(home: Path) -> None:
    """A project run lands artifacts/kernel/results/session under the project root."""
    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.runtime import AgentRuntime

    config = AgentConfig(
        api_key="test-key",
        enable_telemetry=False,
        enable_memory=False,
        persistent_kernel=False,
    )
    proj = _manifest_project(home)

    runtime = AgentRuntime.from_config(config, project=proj)

    assert runtime.project is proj
    assert runtime.run_id is not None and len(runtime.run_id) == 32
    # Artifacts dir IS the project artifacts dir (not a tempdir sibling).
    assert runtime.artifacts_dir == proj.artifacts_dir
    # Session-facing subdirs were materialised under the project root.
    assert proj.session_path(runtime.run_id).parent.is_dir()
    assert proj.kernel_work_dir(runtime.run_id).is_dir()
    assert proj.results_dir_for(runtime.run_id).is_dir()


def test_from_config_without_project_is_unchanged(home: Path) -> None:
    """No project → run_id None, artifacts dir is NOT under any project root."""
    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.runtime import AgentRuntime

    config = AgentConfig(
        api_key="test-key",
        enable_telemetry=False,
        enable_memory=False,
        persistent_kernel=False,
    )
    runtime = AgentRuntime.from_config(config)

    assert runtime.project is None
    assert runtime.run_id is None
    # Without a project, artifacts land in a fresh tempdir, never under projects/.
    assert "projects" not in str(runtime.artifacts_dir)


def test_from_config_no_project_no_stray_kernel_dir(tmp_path: Path) -> None:
    """Regression: persistent_kernel=False must not create a kernel workspace dir.

    Before the workspace slice, ``config.kernel_work_dir`` was only called inside
    the ``if config.persistent_kernel`` guard. The slice must not create a stray
    ``workspace/`` sibling (or a ``daa_kernel_*`` tempdir) when no kernel runs.
    """
    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.runtime import AgentRuntime

    config = AgentConfig(
        api_key="test-key",
        enable_telemetry=False,
        enable_memory=False,
        persistent_kernel=False,
    )
    persist = tmp_path / "sess.jsonl"
    AgentRuntime.from_config(config, persist_path=str(persist))

    # artifacts/ and results/ siblings are expected (the config always builds them);
    # workspace/ must NOT exist because no kernel was requested.
    assert (tmp_path / "artifacts").is_dir()
    assert (tmp_path / "results").is_dir()
    assert not (tmp_path / "workspace").exists()
