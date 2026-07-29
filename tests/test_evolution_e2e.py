"""End-to-end integration of the self-evolution pipeline (Wave 3 / G1).

Proves the FULL closed loop in one run, with the two non-deterministic hops
stubbed (no network / no LLM):

  trajectory → synthesize (stub reflect) → candidate
            → evaluate (stub run_fn)    → proposed_promote + ledger
            → approve (human gate)       → active + ledger
            → build_skill_registry       → loaded into the live registry
            → match_best                 → routes a relevant query to it

The piecewise unit tests (test_synthesizer, test_evaluator, test_skill_ledger)
cover each stage in isolation with hand-seeded inputs; THIS test chains the REAL
output of each stage into the next (a candidate produced by SkillSynthesizer is
consumed by SkillEvaluator; an approved skill is consumed by the runtime's
build_skill_registry + match_best) — the integration surface no single test
covered, and the "self-evolved skill actually reaches the agent" payoff.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_analysis_agent.evolution.evaluator import (
    EvalRun,
    SkillEvaluator,
    approve_skill,
    read_skill_ledger,
)
from data_analysis_agent.evolution.synthesizer import SkillSynthesizer
from data_analysis_agent.runtime import build_skill_registry
from data_analysis_agent.skills.loader import load_skills


def _turn(*, user_input: str, turn_id: str, tools: tuple[str, ...] = ("python_analysis",)) -> dict:
    return {
        "type": "turn",
        "turn_id": turn_id,
        "user_input": user_input,
        "terminal_reason": "COMPLETED",
        "model_turns": 5,
        "active_skill": None,
        "tool_calls": [{"name": t, "is_error": False} for t in tools],
        "feedback": None,
    }


def _write_session(dir_path: Path, turns: list[dict], name: str = "sess.jsonl") -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    with (dir_path / name).open("w", encoding="utf-8") as fh:
        for t in turns:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")


def _eval_task_file(dir_path: Path, task_id: str, text: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{task_id}.json").write_text(
        json.dumps({"task_id": task_id, "input": text, "assertions": {"no_error_results": True}}),
        encoding="utf-8",
    )


def test_self_evolution_closed_loop_trajectory_to_active_skill(tmp_path: Path) -> None:
    """The whole G1 closed loop, end to end, with stubs at the two LLM hops."""
    traj = tmp_path / "traj"
    skills = tmp_path / "skills"
    tasks = tmp_path / "tasks"

    # 1. Seed a recurring uncovered task pattern in real trajectory shape.
    _write_session(
        traj,
        [
            _turn(user_input="做留存 cohort 同期群分析 A", turn_id="a"),
            _turn(user_input="做留存 cohort 同期群分析 B", turn_id="b"),
            _turn(user_input="做留存 cohort 同期群分析 C", turn_id="c"),
        ],
    )

    # 2. synthesize (stub reflect — no LLM): cluster → candidate skill file.
    def fake_reflect(cluster_turns: list[dict]) -> dict | None:
        return {
            "name": "cohort_analysis",
            "description": "用户留存/同期群分析",
            "keywords": ["留存", "cohort", "同期群"],
            "allowed_tools": ["read_file", "python_analysis"],
            "instructions": "1. 解析日期列\n2. 构建同期群矩阵\n3. 计算留存率",
        }

    synth = SkillSynthesizer(traj, skills, fake_reflect, min_occurrences=3)
    paths = synth.synthesize()
    assert len(paths) == 1
    candidate = load_skills(skills, statuses=("candidate",))[0]
    assert candidate.name == "cohort_analysis"
    assert candidate.origin == "synthesized"
    assert set(candidate.source_trajectories) == {"a", "b", "c"}  # provenance survives

    # 3. Seed a relevant eval task (relevant_tasks matches by skill keyword).
    _eval_task_file(tasks, "cohort_1", "做留存 cohort 同期群分析")

    # 4. evaluate (stub run_fn — no LLM/agent): treatment (skill present) wins.
    def run_fn(task, active_skill):
        # control (no skill) errors → fails; treatment (skill) clean → passes.
        return EvalRun(tool_call_count=2, has_error=active_skill is None, final_text="ok")

    evaluator = SkillEvaluator(tasks, skills, run_fn, min_samples=1)
    verdict = evaluator.evaluate(candidate)
    assert verdict["decision"] == "promote"

    # 5. apply → proposed_promote (NOT active; Phase 1 governance: human gate).
    assert evaluator.apply(verdict) is not None
    assert [s.name for s in load_skills(skills, statuses=("proposed_promote",))] == [
        "cohort_analysis"
    ]
    assert load_skills(skills, statuses=("active",)) == []  # no auto-promotion

    # 6. Human gate → active.
    assert approve_skill(skills, "cohort_analysis") == 0
    active = load_skills(skills, statuses=("active",))
    assert [s.name for s in active] == ["cohort_analysis"]

    # 7. Ledger records the whole lifecycle in order (auditable trace).
    actions = [(e["skill"], e["action"], e["to_status"]) for e in read_skill_ledger(skills)]
    assert actions == [
        ("cohort_analysis", "proposed_promote", "proposed_promote"),
        ("cohort_analysis", "approve", "active"),
    ]

    # 8. The approved skill is loaded by the RUNTIME registry — the self-evolution
    #    payoff: a synthesized+promoted skill actually reaches the agent.
    registry = build_skill_registry(skills)
    loaded = registry.get("cohort_analysis")
    assert loaded is not None
    assert "同期群矩阵" in loaded.instructions  # synthesized instructions preserved

    # 9. ...and the registry ROUTES a relevant query to it (match_best), beating
    #    the built-in skills — the closed loop changes agent routing.
    matched = registry.match_best("做留存 cohort 同期群分析")
    assert matched is not None
    assert matched.name == "cohort_analysis"
