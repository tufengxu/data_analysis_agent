"""DeclarativeSkill + data-driven skill loading (L2 evolution carrier).

A skill's whole behaviour is data: instructions injected into the prompt, a
keyword routing list, and a tool allowlist (the builtin ``execute`` only ever
returns an activation marker — agent_loop.py drives the real mechanism). So a
skill can live as a plain record on disk and be loaded at runtime; that is what
makes skills the evolution carrier — the synthesizer writes new ones, the
evaluator promotes them.

Format note: records are JSON, not YAML as the plan sketched. Rationale — the
project has zero YAML and is JSONL throughout; skill files are machine-generated
by the synthesizer first, human-reviewed second; adding PyYAML buys multi-line
readability we don't need enough to justify a new core dependency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from .base import Skill, SkillResult

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")

# Lifecycle: a candidate (synthesized, unproven) is loaded only when explicitly
# requested; active is loaded into the live registry; retired is kept for audit.
SkillStatus = Literal["candidate", "active", "retired"]
SKILL_STATUSES: tuple[SkillStatus, ...] = ("candidate", "active", "retired")


class DeclarativeSkill(Skill):
    """A skill constructed from a plain record rather than a Python subclass."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        keywords: list[str] | None = None,
        allowed_tools: list[str] | None = None,
        status: str = "active",
        origin: str = "synthesized",
        eval_score: float | None = None,
        source_trajectories: list[str] | None = None,
    ) -> None:
        self._name = name
        self._description = description
        self._instructions = instructions
        self._keywords = list(keywords or [])
        self._allowed_tools = list(allowed_tools or [])
        self.status = status
        self.origin = origin
        self.eval_score = eval_score
        self.source_trajectories = list(source_trajectories or [])

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def instructions(self) -> str:
        return self._instructions

    @property
    def keywords(self) -> list[str]:
        return self._keywords

    @property
    def allowed_tools(self) -> list[str]:
        return self._allowed_tools

    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        return SkillResult(
            output=f"{self._name} skill activated for: {query}",
            tools_used=list(self._allowed_tools),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "description": self._description,
            "keywords": self._keywords,
            "allowed_tools": self._allowed_tools,
            "instructions": self._instructions,
            "status": self.status,
            "origin": self.origin,
            "eval_score": self.eval_score,
            "source_trajectories": self.source_trajectories,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeclarativeSkill:
        if not d.get("name") or not isinstance(d.get("instructions"), str):
            raise ValueError("skill record needs at least name + instructions")
        return cls(
            name=str(d["name"]),
            description=str(d.get("description", "")),
            instructions=str(d["instructions"]),
            keywords=list(d.get("keywords", [])),
            allowed_tools=list(d.get("allowed_tools", [])),
            status=str(d.get("status", "active")),
            origin=str(d.get("origin", "synthesized")),
            eval_score=d.get("eval_score"),
            source_trajectories=list(d.get("source_trajectories", [])),
        )


def skill_to_dict(
    skill: Skill, *, status: str = "active", origin: str = "builtin"
) -> dict[str, Any]:
    """Snapshot any Skill as a declarative record (used to migrate builtins)."""
    return {
        "name": skill.name,
        "description": skill.description,
        "keywords": list(skill.keywords),
        "allowed_tools": list(skill.allowed_tools),
        "instructions": skill.instructions,
        "status": status,
        "origin": origin,
        "eval_score": None,
        "source_trajectories": [],
    }


def save_skill(skills_dir: str | Path, record: dict[str, Any]) -> Path:
    """Persist one skill record to ``<dir>/<safe-name>.json``."""
    d = Path(skills_dir)
    d.mkdir(parents=True, exist_ok=True)
    safe = _SAFE_NAME.sub("_", str(record.get("name", "skill"))).strip("._") or "skill"
    path = d / f"{safe}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_skills(
    skills_dir: str | Path, *, statuses: tuple[str, ...] = ("active",)
) -> list[DeclarativeSkill]:
    """Load skill records whose status is in ``statuses`` (malformed files skipped)."""
    d = Path(skills_dir)
    if not d.exists():
        return []
    out: list[DeclarativeSkill] = []
    for path in sorted(d.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(record, dict) or record.get("status", "active") not in statuses:
            continue
        try:
            out.append(DeclarativeSkill.from_dict(record))
        except ValueError:
            continue
    return out
