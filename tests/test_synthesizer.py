"""Tests for Stage D: skill synthesis pipeline (eligibility, clustering, cold-start)."""

import json

from data_analysis_agent.evolution.synthesizer import (
    SkillSynthesizer,
    cluster_uncovered,
    is_eligible,
    keywords,
    load_corpus,
)
from data_analysis_agent.skills.loader import load_skills


def _turn(
    *,
    user_input,
    turn_id="t",
    terminal="COMPLETED",
    model_turns=5,
    active_skill=None,
    feedback=None,
    tools=("python_analysis",),
):
    return {
        "type": "turn",
        "turn_id": turn_id,
        "user_input": user_input,
        "terminal_reason": terminal,
        "model_turns": model_turns,
        "active_skill": active_skill,
        "tool_calls": [{"name": t, "is_error": False} for t in tools],
        "feedback": feedback,
    }


def _write_session(dir_path, turns, name="sess.jsonl"):
    dir_path.mkdir(parents=True, exist_ok=True)
    with (dir_path / name).open("w", encoding="utf-8") as fh:
        for t in turns:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")


# --- eligibility ------------------------------------------------------------


def test_eligibility_filters():
    assert is_eligible(_turn(user_input="留存分析", model_turns=5)) is True
    assert is_eligible(_turn(user_input="x", terminal="MAX_TURNS")) is False
    assert is_eligible(_turn(user_input="x", model_turns=2)) is False
    assert is_eligible(_turn(user_input="x", feedback={"kind": "bad"})) is False
    assert is_eligible(_turn(user_input="x", feedback={"kind": "rephrase"})) is False


# --- clustering -------------------------------------------------------------


def test_clusters_recurring_uncovered_tasks():
    turns = [
        _turn(user_input="帮我做用户留存分析 cohort", turn_id="a"),
        _turn(user_input="留存 cohort 同期群分析", turn_id="b"),
        _turn(user_input="做个留存 cohort 报告", turn_id="c"),
        _turn(user_input="今天天气怎么样", turn_id="d"),  # singleton, dropped
    ]
    clusters = cluster_uncovered(turns, min_occurrences=3)
    assert len(clusters) == 1
    assert {t["turn_id"] for t in clusters[0].turns} == {"a", "b", "c"}


def test_covered_tasks_excluded_from_clustering():
    # Tasks already handled by an active skill are NOT candidates for new skills.
    turns = [
        _turn(user_input="留存分析", turn_id="a", active_skill="cohort_analysis"),
        _turn(user_input="留存分析", turn_id="b", active_skill="cohort_analysis"),
        _turn(user_input="留存分析", turn_id="c", active_skill="cohort_analysis"),
    ]
    assert cluster_uncovered(turns, min_occurrences=3) == []


def test_keywords_drops_stopwords():
    kw = keywords("帮我分析一下留存 cohort 数据")
    assert "留存" in kw and "cohort" in kw
    assert "分析" not in kw and "数据" not in kw


# --- corpus + full pipeline (cold-start self-check) -------------------------


def test_load_corpus_merges_sessions(tmp_path):
    traj = tmp_path / "traj"
    _write_session(traj, [_turn(user_input="a")], name="s1.jsonl")
    _write_session(traj, [_turn(user_input="b")], name="s2.jsonl")
    assert len(load_corpus(traj)) == 2


def test_synthesize_cold_start_pipeline(tmp_path):
    """Cold-start self-check: a recurring uncovered task cluster reflects into a
    candidate skill file. Reflection is stubbed (no network) — this verifies the
    deterministic pipeline (load → eligible → cluster → reflect → persist)."""
    traj = tmp_path / "traj"
    skills = tmp_path / "skills"
    _write_session(
        traj,
        [
            _turn(user_input="留存 cohort 同期群分析 A", turn_id="a"),
            _turn(user_input="留存 cohort 同期群分析 B", turn_id="b"),
            _turn(user_input="留存 cohort 同期群分析 C", turn_id="c"),
        ],
    )

    def fake_reflect(cluster_turns):
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
    candidates = load_skills(skills, statuses=("candidate",))
    assert len(candidates) == 1
    skill = candidates[0]
    assert skill.name == "cohort_analysis"
    assert skill.status == "candidate"  # never auto-active
    assert skill.origin == "synthesized"
    assert set(skill.source_trajectories) == {"a", "b", "c"}  # provenance kept


def test_synthesize_skips_invalid_reflection(tmp_path):
    traj = tmp_path / "traj"
    skills = tmp_path / "skills"
    _write_session(
        traj,
        [_turn(user_input="漏斗 funnel 转化分析", turn_id=str(i)) for i in range(3)],
    )
    synth = SkillSynthesizer(traj, skills, lambda turns: {"name": "x"})  # no instructions
    assert synth.synthesize() == []


def test_synthesize_requires_min_occurrences(tmp_path):
    traj = tmp_path / "traj"
    skills = tmp_path / "skills"
    _write_session(
        traj,
        [
            _turn(user_input="漏斗 funnel 分析", turn_id="a"),
            _turn(user_input="漏斗 funnel 分析", turn_id="b"),
        ],
    )
    synth = SkillSynthesizer(traj, skills, lambda turns: {"name": "f", "instructions": "x"})
    assert synth.synthesize() == []  # only 2 occurrences < 3
