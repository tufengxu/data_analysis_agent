"""Tests for Stage E: eval harness (assertions, sample gate, promote/rollback)."""

import json

from data_analysis_agent.evolution.evaluator import (
    EvalResult,
    EvalRun,
    EvalTask,
    SkillEvaluator,
    check_assertions,
    decide_promotion,
    load_eval_tasks,
    relevant_tasks,
)
from data_analysis_agent.skills.loader import DeclarativeSkill, load_skills, save_skill

# --- assertions (method/structure, not numbers) -----------------------------


def test_check_assertions_pass_and_fail():
    run = EvalRun(tool_call_count=3, has_error=False, final_text="同期群矩阵已构建")
    ok, failures = check_assertions(
        run, {"no_error_results": True, "tool_call_count_max": 5, "final_text_contains": ["同期群"]}
    )
    assert ok and failures == []

    bad = EvalRun(tool_call_count=9, has_error=True, final_text="done")
    ok, failures = check_assertions(
        bad, {"no_error_results": True, "tool_call_count_max": 5, "final_text_contains": ["留存"]}
    )
    assert not ok
    assert len(failures) == 3  # error + over cap + missing text


def test_check_assertions_regex():
    run = EvalRun(2, False, "生成了 3 张图表")
    ok, _ = check_assertions(run, {"final_text_regex": r"\d+ 张图表"})
    assert ok


def test_load_eval_tasks(tmp_path):
    (tmp_path / "t.json").write_text(
        json.dumps(
            {"task_id": "x", "input": "做留存分析", "assertions": {"no_error_results": True}}
        ),
        encoding="utf-8",
    )
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    tasks = load_eval_tasks(tmp_path)
    assert len(tasks) == 1 and tasks[0].task_id == "x"


def test_relevant_tasks_by_keyword():
    skill = DeclarativeSkill(
        name="cohort_analysis", description="d", instructions="i", keywords=["留存", "cohort"]
    )
    tasks = [
        EvalTask("a", "帮我做留存分析"),
        EvalTask("b", "cohort 同期群"),
        EvalTask("c", "今天天气如何"),
    ]
    relevant = relevant_tasks(skill, tasks)
    assert {t.task_id for t in relevant} == {"a", "b"}


# --- promotion decision + sample gate ---------------------------------------


def _pairs(n, *, treatment_pass, control_pass, t_tools=2, c_tools=2):
    pairs = []
    for i in range(n):
        c = EvalResult(f"t{i}", i < control_pass, [], c_tools)
        t = EvalResult(f"t{i}", i < treatment_pass, [], t_tools)
        pairs.append((c, t))
    return pairs


def test_sample_gate_blocks_promotion_below_min():
    decision, metrics = decide_promotion(_pairs(3, treatment_pass=3, control_pass=0), min_samples=5)
    assert decision == "needs_review"
    assert metrics["n"] == 3


def test_promote_when_no_regression():
    decision, _ = decide_promotion(
        _pairs(5, treatment_pass=5, control_pass=3, t_tools=2, c_tools=3), min_samples=5
    )
    assert decision == "promote"


def test_retire_on_quality_regression():
    decision, _ = decide_promotion(_pairs(5, treatment_pass=2, control_pass=4), min_samples=5)
    assert decision == "retire"


def test_retire_on_cost_regression():
    # Same pass rate but the skill makes the agent do MORE work → retire.
    decision, _ = decide_promotion(
        _pairs(5, treatment_pass=5, control_pass=5, t_tools=6, c_tools=3), min_samples=5
    )
    assert decision == "retire"


# --- end-to-end with injected run_fn ----------------------------------------


def _eval_task_file(dir_path, task_id, text):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{task_id}.json").write_text(
        json.dumps({"task_id": task_id, "input": text, "assertions": {"no_error_results": True}}),
        encoding="utf-8",
    )


def test_evaluator_promotes_and_rewrites_status(tmp_path):
    tasks_dir = tmp_path / "tasks"
    skills_dir = tmp_path / "skills"
    for i in range(5):
        _eval_task_file(tasks_dir, f"cohort_{i}", "做留存 cohort 分析")

    save_skill(
        skills_dir,
        {
            "name": "cohort_analysis",
            "description": "留存分析",
            "keywords": ["留存", "cohort"],
            "instructions": "构建同期群矩阵",
            "status": "candidate",
        },
    )
    skill = load_skills(skills_dir, statuses=("candidate",))[0]

    # Treatment (skill present) never errors; control errors → treatment wins.
    def run_fn(task, active_skill):
        has_error = active_skill is None
        return EvalRun(tool_call_count=2, has_error=has_error, final_text="ok")

    evaluator = SkillEvaluator(tasks_dir, skills_dir, run_fn, min_samples=5)
    verdict = evaluator.evaluate(skill)
    assert verdict["decision"] == "promote"
    evaluator.apply(verdict)

    # Phase 1 governance: promote -> proposed_promote, NOT active (no auto-promotion;
    # a human must run `evolution approve`). The live registry (active) stays empty.
    proposed = load_skills(skills_dir, statuses=("proposed_promote",))
    assert [s.name for s in proposed] == ["cohort_analysis"]
    assert proposed[0].eval_score == 1.0
    assert load_skills(skills_dir, statuses=("active",)) == []

    # The human gate (approve) is the only path to active.
    from data_analysis_agent.evolution.evaluator import approve_skill

    assert approve_skill(skills_dir, "cohort_analysis") == 0
    active = load_skills(skills_dir, statuses=("active",))
    assert [s.name for s in active] == ["cohort_analysis"]


def test_evaluator_needs_review_keeps_candidate(tmp_path):
    tasks_dir = tmp_path / "tasks"
    skills_dir = tmp_path / "skills"
    _eval_task_file(tasks_dir, "only_one", "做留存分析")  # 1 < min_samples

    save_skill(
        skills_dir,
        {
            "name": "cohort_analysis",
            "keywords": ["留存"],
            "instructions": "x",
            "status": "candidate",
        },
    )
    skill = load_skills(skills_dir, statuses=("candidate",))[0]
    evaluator = SkillEvaluator(tasks_dir, skills_dir, lambda t, s: EvalRun(1, False, "ok"))
    verdict = evaluator.evaluate(skill)

    assert verdict["decision"] == "needs_review"
    assert evaluator.apply(verdict) is None  # unchanged
    assert load_skills(skills_dir, statuses=("candidate",))[0].status == "candidate"


def test_seed_eval_tasks_load():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "examples" / "eval_tasks"
    tasks = load_eval_tasks(root)
    assert any(t.task_id == "descriptive_smoke" for t in tasks)


def test_evaluator_isolates_run_fn_crash(tmp_path):
    """m1 regression: a crashing run_fn must not kill the batch — recorded as fail."""
    tasks_dir = tmp_path / "tasks"
    skills_dir = tmp_path / "skills"
    for i in range(5):
        _eval_task_file(tasks_dir, f"t_{i}", "做留存 cohort 分析")
    save_skill(
        skills_dir,
        {
            "name": "cohort_analysis",
            "keywords": ["留存"],
            "instructions": "x",
            "status": "candidate",
        },
    )
    skill = load_skills(skills_dir, statuses=("candidate",))[0]

    def boom(task, active_skill):
        raise RuntimeError("agent blew up")

    evaluator = SkillEvaluator(tasks_dir, skills_dir, boom, min_samples=5)
    verdict = evaluator.evaluate(skill)  # must not raise
    assert verdict["decision"] == "retire"  # all runs failed → no promotion


def test_check_assertions_bare_string_contains():
    """n3 regression: a bare string for final_text_contains must not iterate per-char."""
    ok, _ = check_assertions(
        EvalRun(1, False, "构建了同期群矩阵"), {"final_text_contains": "同期群"}
    )
    assert ok
    ok2, failures = check_assertions(
        EvalRun(1, False, "no match"), {"final_text_contains": "同期群"}
    )
    assert not ok2 and len(failures) == 1


def test_resolve_task_input_anchors_fixture(tmp_path):
    """E regression: relative fixture ref rewritten to absolute under base dir."""
    from data_analysis_agent.evolution.evaluator import resolve_task_input

    task = EvalTask("t", "分析 fixtures/sales.csv 的分布", dataset_fixture="fixtures/sales.csv")
    out = resolve_task_input(task, tmp_path)
    abs_path = str((tmp_path / "fixtures/sales.csv").resolve())
    assert abs_path in out  # rewritten to an absolute path under base dir
    assert out != task.input

    # No fixture / no base → unchanged.
    assert resolve_task_input(EvalTask("t", "无 fixture"), tmp_path) == "无 fixture"
    assert resolve_task_input(task, None) == task.input


def test_eval_config_decouples_permission_and_isolates():
    """M1/M2 regression: eval config must neutralize env permission policy and
    disable kernel/memory/telemetry, with a fresh (non-shared) deny list."""
    from dataclasses import replace

    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.evolution.evaluator import eval_config_for

    prod = replace(AgentConfig(), permission_mode="plan", deny_patterns=["python_analysis"])
    ev = eval_config_for(prod)
    assert ev.permission_mode == "default"  # not inherited 'plan'
    assert ev.deny_patterns == [] and ev.deny_patterns is not prod.deny_patterns
    assert ev.persistent_kernel is False
    assert ev.enable_memory is False and ev.enable_telemetry is False


def test_eval_config_tool_set_executable_under_plan_env():
    """The eval runtime must still expose executable analysis tools even when the
    production config was plan-mode (which would otherwise deny them)."""
    from dataclasses import replace

    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.evolution.evaluator import eval_config_for
    from data_analysis_agent.runtime import AgentRuntime

    class _FakeClient:
        model = "dummy"

    prod = replace(AgentConfig(), permission_mode="plan")
    runtime = AgentRuntime.from_config(eval_config_for(prod), client=_FakeClient())
    tools = set(runtime.loop.registry.list_tools())
    assert "python_analysis" in tools and "html_report" in tools  # not denied away


def test_approve_and_retire_lifecycle(tmp_path):
    """approve_skill/retire_skill: idempotent, not-found=1, and the only
    active-writing / demotion paths."""
    from data_analysis_agent.evolution.evaluator import approve_skill, retire_skill

    skills_dir = tmp_path / "skills"
    save_skill(
        skills_dir,
        {"name": "s1", "keywords": ["k"], "instructions": "do x", "status": "candidate"},
    )

    # not-found
    assert approve_skill(skills_dir, "missing") == 1
    assert retire_skill(skills_dir, "missing") == 1

    # candidate -> proposed_promote (via apply) -> active (via approve, idempotent)
    assert approve_skill(skills_dir, "s1") == 0
    assert approve_skill(skills_dir, "s1") == 0  # already active -> idempotent 0
    assert load_skills(skills_dir, statuses=("active",))[0].name == "s1"

    # active -> retired (via retire, idempotent)
    assert retire_skill(skills_dir, "s1") == 0
    assert retire_skill(skills_dir, "s1") == 0  # already retired -> idempotent 0
    assert load_skills(skills_dir, statuses=("retired",))[0].name == "s1"
    assert load_skills(skills_dir, statuses=("active",)) == []
