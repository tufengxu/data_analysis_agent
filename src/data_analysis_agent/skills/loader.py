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
import logging
import re
from pathlib import Path
from typing import Any, Literal

from ..disk_cap import enforce_dir_disk_cap
from ..security.sanitizer import has_injection_marker
from .base import Skill, SkillResult

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")

# Lifecycle: a candidate (synthesized, unproven) is loaded only when explicitly
# requested; proposed_promote = passed eval but awaiting explicit human approval
# (Phase 1: NO auto-promotion — roadmap non-goal); active is loaded into the live
# registry; retired is kept for audit.
SkillStatus = Literal["candidate", "proposed_promote", "active", "retired"]
SKILL_STATUSES: tuple[SkillStatus, ...] = ("candidate", "proposed_promote", "active", "retired")


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


# Disk cap on the skills dir. Candidate skills accumulate as self-evolution runs
# (synthesizer writes them, evaluator promotes a few); without a cap the dir grows
# without bound. Active / proposed_promote skills are protected (live use /
# awaiting human promotion review); retired is evicted first, then oldest
# candidates — both by byte budget, never by sheer file count alone.
_MAX_SKILL_DIR_BYTES = 128 * 1024 * 1024
# Eviction rank: corrupt/unreadable (0) → retired (1) → candidate (2). A corrupt
# file is pure dead weight, evicted before a valid retired skill.
_SKILL_EVICT_STATUS_RANK = {"unknown": 0, "retired": 1, "candidate": 2}


def _read_skill_status(path: Path) -> str:
    """Best-effort status read for eviction ranking. Malformed → evictable."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "unknown"
    return str(record.get("status", "active")) if isinstance(record, dict) else "unknown"


def _enforce_skill_disk_cap(skills_dir: Path, max_bytes: int) -> int:
    """Best-effort cap on the skills dir via the shared helper.

    Protected: ``active`` / ``proposed_promote``. Eviction rank: corrupt /
    unreadable (0) → ``retired`` (1) → ``candidate`` (2), oldest mtime first
    within each bucket. A skill missing ``status`` defaults to ``active``
    (protected) — matches ``load_skills``.
    """
    statuses = {p: _read_skill_status(p) for p in skills_dir.glob("*.json")}

    def is_protected(p: Path) -> bool:
        return statuses.get(p) in ("active", "proposed_promote")

    def rank(p: Path) -> tuple[int, float]:
        bucket = _SKILL_EVICT_STATUS_RANK.get(statuses.get(p, ""), 2)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (bucket, mtime)

    return enforce_dir_disk_cap(
        skills_dir,
        max_bytes,
        pattern="*.json",
        is_protected=is_protected,
        eviction_rank=rank,
    )


def save_skill(
    skills_dir: str | Path,
    record: dict[str, Any],
    *,
    max_dir_bytes: int = _MAX_SKILL_DIR_BYTES,
) -> Path | None:
    """Persist one skill record to ``<dir>/<safe-name>.json``.

    Returns None (and refuses to write) if the instructions carry a structural
    prompt-injection marker — a synthesized skill must never need role spoofing /
    control tokens / override directives. Built-in records never trip this.

    After the write, best-effort enforce the skills-dir disk cap (evict corrupt
    → retired → oldest candidate; active / proposed_promote are protected).
    """
    instructions = record.get("instructions", "")
    if isinstance(instructions, str) and has_injection_marker(instructions):
        logger.warning(
            "rejecting skill %r: instructions contain a prompt-injection marker",
            record.get("name"),
        )
        return None
    d = Path(skills_dir)
    d.mkdir(parents=True, exist_ok=True)
    safe = _SAFE_NAME.sub("_", str(record.get("name", "skill"))).strip("._") or "skill"
    path = d / f"{safe}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    _enforce_skill_disk_cap(d, max_dir_bytes)
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
