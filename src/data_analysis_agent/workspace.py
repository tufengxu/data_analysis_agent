"""Project workspace: unify a run's session-facing durable state under one root.

Slice 1 of roadmap P1-2. A ``Project`` gives every analysis a reproducible,
inspectable home: artifacts, kernel workspace, results, and the session message
store all land under ``<root>/``, and each run records a manifest under
``<root>/runs/<run_id>.json`` plus an entry in ``project.json``.

Scope note: trajectories / memory / skills stay on their global ``~/.daa`` roots
for now (project-scoping them is P1-5); ``project.json`` only records the
session-facing layout. The project is opt-in — without one, the runtime behaves
exactly as before.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def default_home() -> Path:
    """Cross-session root, mirroring ``AgentConfig.daa_home`` (DAA_HOME / ~/.daa)."""
    return Path(os.environ.get("DAA_HOME", str(Path.home() / ".daa")))


def _utcnow_iso(now: Callable[[], Any] | None = None) -> str:
    """ISO-8601 UTC timestamp; ``now`` lets tests pin the value."""
    from datetime import datetime, timezone

    dt = now() if now is not None else datetime.now(timezone.utc)
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON via tmp + os.replace so a crash cannot leave a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Session-facing subdirectories created for every project (trajectory/memory/skill
# dirs stay on the global ~/.daa root by design — see module docstring).
_PROJECT_SUBDIRS: tuple[str, ...] = (
    "sessions",
    "artifacts",
    "results",
    "workspace",
    "runs",
    "uploads",
    "logs",
)


@dataclass
class ProjectManifest:
    """The durable, atomically-rewritten ``project.json`` payload."""

    project_id: str
    created_at: str
    root: str
    authorized_paths: list[str] = field(default_factory=list)
    model: str = ""
    preset: str = ""
    runs: list[str] = field(default_factory=list)  # run_id index, oldest-first

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "created_at": self.created_at,
            "root": self.root,
            "authorized_paths": list(self.authorized_paths),
            "model": self.model,
            "preset": self.preset,
            "runs": list(self.runs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectManifest:
        """Tolerant load: ignore unknown keys, default missing ones."""
        return cls(
            project_id=str(data.get("project_id", "")),
            created_at=str(data.get("created_at", "")),
            root=str(data.get("root", "")),
            authorized_paths=[str(p) for p in data.get("authorized_paths", [])],
            model=str(data.get("model", "")),
            preset=str(data.get("preset", "")),
            runs=[str(r) for r in data.get("runs", [])],
        )


@dataclass
class RunManifest:
    """Per-run record written under ``<project>/runs/<run_id>.json``."""

    run_id: str
    project_id: str
    started_at: str
    finished_at: str | None
    request: str
    authorized_paths: list[str]
    session_id: str
    event_counts: dict[str, int]
    tool_calls: dict[str, int]
    artifacts: list[str]
    terminal_reason: str | None
    token_usage: dict[str, int] | None
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "request": self.request,
            "authorized_paths": list(self.authorized_paths),
            "session_id": self.session_id,
            "event_counts": dict(self.event_counts),
            "tool_calls": dict(self.tool_calls),
            "artifacts": list(self.artifacts),
            "terminal_reason": self.terminal_reason,
            "token_usage": dict(self.token_usage) if self.token_usage is not None else None,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunManifest:
        return cls(
            run_id=str(data.get("run_id", "")),
            project_id=str(data.get("project_id", "")),
            started_at=str(data.get("started_at", "")),
            finished_at=data.get("finished_at"),
            request=str(data.get("request", "")),
            authorized_paths=[str(p) for p in data.get("authorized_paths", [])],
            session_id=str(data.get("session_id", "")),
            event_counts={str(k): int(v) for k, v in data.get("event_counts", {}).items()},
            tool_calls={str(k): int(v) for k, v in data.get("tool_calls", {}).items()},
            artifacts=[str(a) for a in data.get("artifacts", [])],
            terminal_reason=data.get("terminal_reason"),
            token_usage=(
                {str(k): int(v) for k, v in data["token_usage"].items()}
                if data.get("token_usage")
                else None
            ),
            warnings=[str(w) for w in data.get("warnings", [])],
        )


@dataclass
class Project:
    """A workspace root tying one analysis project's session state together."""

    project_id: str
    root: Path
    manifest: ProjectManifest

    # --- layout -----------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.root / "project.json"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    @property
    def workspace_dir(self) -> Path:
        return self.root / "workspace"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def uploads_dir(self) -> Path:
        return self.root / "uploads"

    def session_path(self, run_id: str) -> Path:
        return self.sessions_dir / f"{run_id}.jsonl"

    def kernel_work_dir(self, run_id: str) -> Path:
        return self.workspace_dir / run_id

    def results_dir_for(self, run_id: str) -> Path:
        return self.results_dir / run_id

    def run_manifest_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    # --- lifecycle --------------------------------------------------------
    @classmethod
    def init(
        cls,
        project_id: str,
        *,
        home: Path | None = None,
        path: str | Path | None = None,
        authorized_paths: Sequence[str] = (),
        model: str = "",
        preset: str = "",
        now: Callable[[], Any] | None = None,
    ) -> Project:
        """Create the project root + subdirs and a fresh project.json.

        Idempotent on the directory tree; if ``project.json`` already exists it is
        loaded unchanged (re-init does not clobber ``created_at`` or the run index).
        """
        home = home or default_home()
        root = Path(path).expanduser() if path else home / "projects" / project_id
        root.mkdir(parents=True, exist_ok=True)
        for sub in _PROJECT_SUBDIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        manifest_path = root / "project.json"
        if manifest_path.exists():
            manifest = ProjectManifest.from_dict(_load_json(manifest_path))
        else:
            manifest = ProjectManifest(
                project_id=project_id,
                created_at=_utcnow_iso(now),
                root=str(root),
                authorized_paths=list(authorized_paths),
                model=model,
                preset=preset,
                runs=[],
            )
            _atomic_write_json(manifest_path, manifest.to_dict())
        return cls(project_id=project_id, root=root, manifest=manifest)

    @classmethod
    def open(cls, project_id: str, *, home: Path | None = None) -> Project:
        """Load an existing project by id; raise KeyError if it does not exist."""
        home = home or default_home()
        root = home / "projects" / project_id
        manifest_path = root / "project.json"
        if not manifest_path.is_file():
            raise KeyError(f"project not found: {project_id!r}")
        manifest = ProjectManifest.from_dict(_load_json(manifest_path))
        return cls(project_id=manifest.project_id or project_id, root=root, manifest=manifest)

    @classmethod
    def open_path(cls, path: str | Path) -> Project:
        """Load a project whose root is ``path`` (must contain project.json)."""
        root = Path(path).expanduser()
        manifest_path = root / "project.json"
        if not manifest_path.is_file():
            raise KeyError(f"project not found at path: {path!r}")
        manifest = ProjectManifest.from_dict(_load_json(manifest_path))
        return cls(project_id=manifest.project_id, root=root, manifest=manifest)

    @classmethod
    def list_projects(cls, *, home: Path | None = None) -> list[Project]:
        """All projects under ``<home>/projects/``, sorted by id, skipping corrupt.

        Named ``list_projects`` rather than ``list`` so the builtin ``list`` stays
        usable as a type annotation elsewhere in this class.
        """
        home = home or default_home()
        projects_dir = home / "projects"
        if not projects_dir.is_dir():
            return []
        found: list[Project] = []
        for child in sorted(projects_dir.iterdir()):
            manifest_path = child / "project.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = ProjectManifest.from_dict(_load_json(manifest_path))
            except (OSError, ValueError):
                continue
            found.append(
                cls(project_id=manifest.project_id or child.name, root=child, manifest=manifest)
            )
        return found

    # --- mutation ---------------------------------------------------------
    def add_run(self, run: RunManifest) -> Path:
        """Persist a run manifest and append its id to the project index.

        Both writes are atomic (tmp + os.replace); the index append is last so a
        crash never records a run id whose manifest failed to land.
        """
        run_path = self.run_manifest_path(run.run_id)
        _atomic_write_json(run_path, run.to_dict())
        if run.run_id not in self.manifest.runs:
            self.manifest.runs.append(run.run_id)
            _atomic_write_json(self.manifest_path, self.manifest.to_dict())
        return run_path

    def history(self) -> list[RunManifest]:
        """Runs newest-first; skips ids whose manifest file is missing/corrupt."""
        runs: list[RunManifest] = []
        for run_id in reversed(self.manifest.runs):
            run_path = self.run_manifest_path(run_id)
            if not run_path.is_file():
                continue
            try:
                runs.append(RunManifest.from_dict(_load_json(run_path)))
            except (OSError, ValueError):
                continue
        return runs


def new_run_id() -> str:
    """Fresh run id (uuid4 hex); centralised so callers and tests share one shape."""
    return uuid.uuid4().hex
