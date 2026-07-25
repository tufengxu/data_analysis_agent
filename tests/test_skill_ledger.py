"""Tests for the skill promotion regression ledger (P1-6.5, Wave 3 Slice 1).

Every evaluate proposal / approve / retire must leave a dated, attributed,
append-only trace in <skills_dir>/ledger.jsonl.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_analysis_agent.evolution.evaluator import (
    SkillEvaluator,
    approve_skill,
    read_skill_ledger,
    retire_skill,
)
from data_analysis_agent.skills.loader import save_skill


def _save(skills_dir: Path, name: str, status: str) -> None:
    save_skill(
        skills_dir,
        {"name": name, "keywords": ["k"], "instructions": "x", "status": status},
    )


def test_approve_writes_ledger(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _save(skills, "s1", "candidate")
    assert approve_skill(skills, "s1") == 0
    entries = read_skill_ledger(skills)
    assert len(entries) == 1
    e = entries[0]
    assert (e["skill"], e["action"], e["from_status"], e["to_status"]) == (
        "s1",
        "approve",
        "candidate",
        "active",
    )
    assert e["decided_at"] and "actor" in e


def test_retire_writes_ledger(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _save(skills, "s1", "active")
    assert retire_skill(skills, "s1") == 0
    entries = read_skill_ledger(skills)
    assert len(entries) == 1
    assert entries[0]["action"] == "retire"
    assert entries[0]["to_status"] == "retired"


def test_noop_does_not_write(tmp_path: Path) -> None:
    """Idempotent no-op (already in target state) stays silent in the ledger."""
    skills = tmp_path / "skills"
    _save(skills, "s1", "active")
    assert approve_skill(skills, "s1") == 0
    assert read_skill_ledger(skills) == []


def test_ledger_accumulates_append_only(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _save(skills, "s1", "candidate")
    approve_skill(skills, "s1")
    retire_skill(skills, "s1")
    entries = read_skill_ledger(skills)
    assert [e["action"] for e in entries] == ["approve", "retire"]
    assert [e["to_status"] for e in entries] == ["active", "retired"]


def test_evaluator_apply_writes_ledger_with_metrics(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _save(skills, "s1", "candidate")
    evaluator = SkillEvaluator(tmp_path / "tasks", skills, lambda t, s: None)
    evaluator.apply({"skill": "s1", "decision": "promote", "metrics": {"pass_rate": 0.8, "n": 5}})
    entries = read_skill_ledger(skills)
    assert len(entries) == 1
    e = entries[0]
    assert e["action"] == "proposed_promote"
    assert e["to_status"] == "proposed_promote"
    assert e["eval_score"] == 0.8
    assert e["metrics"] == {"pass_rate": 0.8, "n": 5}


def test_read_ledger_filters_by_name_and_skips_corrupt(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "ledger.jsonl").write_text(
        json.dumps({"skill": "a", "action": "approve"})
        + "\n"
        + "{ corrupt line\n"
        + json.dumps({"skill": "b", "action": "retire"})
        + "\n",
        encoding="utf-8",
    )
    assert [e["skill"] for e in read_skill_ledger(skills)] == ["a", "b"]
    assert [e["skill"] for e in read_skill_ledger(skills, "b")] == ["b"]


def test_read_ledger_missing_returns_empty(tmp_path: Path) -> None:
    assert read_skill_ledger(tmp_path / "nosuch") == []
