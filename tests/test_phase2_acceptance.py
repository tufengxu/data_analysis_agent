"""Phase 2 acceptance: the evaluate→decide loop closes (not stuck at needs_review).

Chain: trajectories (2A enriched) → synthesizer candidate → harvester ≥5 tasks
→ evaluator decide_promotion ∈ {promote, retire}.
"""

import json

from data_analysis_agent.evolution.eval_harvester import harvest_eval_tasks
from data_analysis_agent.evolution.evaluator import (
    EvalRun,
    SkillEvaluator,
)
from data_analysis_agent.evolution.synthesizer import SkillSynthesizer, load_corpus


def _write_turn(dir_path, turn_id, user_input):
    dir_path.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "turn",
        "session_id": "s",
        "turn_id": turn_id,
        "ts_start": "",
        "ts_end": "",
        "user_input": user_input,
        "active_skill": None,
        "tool_calls": [
            {
                "name": "data_profile",
                "is_error": False,
                "duration_ms": 10,
                "result_chars": 100,
                "input_digest": '{"path":"<path:sales.csv>"}',
                "referenced_files": ["sales.csv"],
            },
            {
                "name": "python_analysis",
                "is_error": False,
                "duration_ms": 20,
                "result_chars": 200,
                "input_digest": '{"code":"df=pd.read_csv(...)"}',
                "referenced_files": [],
            },
        ],
        "terminal_reason": "COMPLETED",
        "model_turns": 5,
        "tokens": {},
        "final_text_digest": "",
    }
    (dir_path / f"{turn_id}.jsonl").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def test_phase2_loop_promotes(tmp_path):
    traj = tmp_path / "traj"
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "sales.csv").write_text("region,sales\n北,10\n南,20\n", encoding="utf-8")
    # 6 distinct inputs → 6 harvested tasks; all share 销售 bigram → 1 cluster
    for i in range(6):
        _write_turn(traj, f"t{i}", f"销售分析 第{i}批 用 sales.csv")

    # --- 2B: harvest ≥5 relevant tasks ---
    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    written = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])
    assert len(written) >= 5

    # --- synthesizer: 1 candidate skill from the cluster ---
    def reflect_fn(turns):
        return {
            "name": "sales-analysis",
            "keywords": ["销售"],
            "instructions": "描述性统计销售数据",
        }

    skills_dir = tmp_path / "skills"
    synth = SkillSynthesizer(traj, skills_dir, reflect_fn, min_occurrences=3, min_model_turns=4)
    candidate_files = synth.synthesize()
    assert len(candidate_files) == 1

    # --- evaluator: decide over the harvested task set ---
    def run_fn(task, skill):
        # treatment (skill present) is cheaper; both pass
        return EvalRun(
            tool_call_count=3 if skill is not None else 4, has_error=False, final_text="done"
        )

    ev = SkillEvaluator(eval_dir, skills_dir, run_fn, min_samples=5)
    from data_analysis_agent.skills.loader import load_skills

    candidate = load_skills(skills_dir, statuses=("candidate",))[0]
    verdict = ev.evaluate(candidate)

    assert verdict["decision"] == "promote"
    assert verdict["decision"] != "needs_review"
    assert verdict["metrics"]["n"] >= 5
