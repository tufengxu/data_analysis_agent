"""SkillEvaluator: gate candidate skills by rerunning them on frozen fixtures.

Uses the data-analysis-only lever: a skill is an executable recipe, so it can
be RUN on a frozen dataset and checked — far more objective than an LLM judge.

Two deliberate constraints (ADR 0005):
* Assertions verify METHOD/STRUCTURE, never specific numbers — data drifts, so
  asserting "留存率==12%" would rot; assert "no error / produced a chart" instead.
* Minimum-sample gate — A/B promote/rollback needs enough relevant tasks to be
  meaningful; below the gate we DON'T auto-promote, we down-shift to human review
  (so cold-start noise can't silently promote a bad skill).
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..skills.base import Skill
from ..skills.loader import load_skills, save_skill

logger = logging.getLogger(__name__)

MIN_SAMPLES = 5

# (task, skill_or_None) -> observed run. skill=None is the control arm.
RunFn = Callable[["EvalTask", "Skill | None"], "EvalRun"]


@dataclass
class EvalTask:
    task_id: str
    input: str
    assertions: dict[str, Any] = field(default_factory=dict)
    dataset_fixture: str | None = None


@dataclass
class EvalRun:
    """What was observed running one task (no numeric claims, by design)."""

    tool_call_count: int
    has_error: bool
    final_text: str
    tools_used: tuple[str, ...] = ()  # Wave 7: tool names from ToolResultEvent
    artifact_paths: tuple[str, ...] = ()  # Wave 7: persisted artifact paths
    artifact_sections: tuple[str, ...] = ()  # Wave 7.5: HTML section markers


@dataclass
class EvalResult:
    task_id: str
    passed: bool
    failures: list[str]
    tool_call_count: int


def load_eval_tasks(tasks_dir: str | Path) -> list[EvalTask]:
    d = Path(tasks_dir)
    if not d.exists():
        return []
    tasks: list[EvalTask] = []
    for path in sorted(d.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(rec, dict) and rec.get("task_id") and rec.get("input"):
            tasks.append(
                EvalTask(
                    task_id=str(rec["task_id"]),
                    input=str(rec["input"]),
                    assertions=dict(rec.get("assertions", {})),
                    dataset_fixture=rec.get("dataset_fixture"),
                )
            )
    return tasks


def check_assertions(run: EvalRun, assertions: dict[str, Any]) -> tuple[bool, list[str]]:
    """Method/structure checks only — never assert a specific numeric value."""
    failures: list[str] = []
    if assertions.get("no_error_results") and run.has_error:
        failures.append("a tool result was an error")
    cap = assertions.get("tool_call_count_max")
    if isinstance(cap, int) and run.tool_call_count > cap:
        failures.append(f"tool_call_count {run.tool_call_count} > {cap}")
    floor = assertions.get("min_tool_calls")
    if isinstance(floor, int) and run.tool_call_count < floor:
        failures.append(f"tool_call_count {run.tool_call_count} < {floor}")
    contains = assertions.get("final_text_contains", [])
    if isinstance(contains, str):  # guard: a bare string would iterate per-char
        contains = [contains]
    for needle in contains:
        if str(needle) not in run.final_text:
            failures.append(f"final text missing: {needle}")
    pattern = assertions.get("final_text_regex")
    if isinstance(pattern, str) and not re.search(pattern, run.final_text):
        failures.append(f"final text did not match /{pattern}/")
    required = assertions.get("required_tools")
    if isinstance(required, str):  # 裸字符串守卫(同 final_text_contains)
        required = [required]
    if isinstance(required, list):
        for tool in required:
            if tool not in run.tools_used:
                failures.append(f"required tool missing: {tool}")
    # artifact_produced:truthy → 要求至少一个 artifact 路径;falsy → 不检查(可选断言惯例)
    if assertions.get("artifact_produced") and not run.artifact_paths:
        failures.append("no artifact produced")
    required_sections = assertions.get("artifact_has_sections")
    if isinstance(required_sections, list):
        for section in required_sections:
            if section not in run.artifact_sections:
                failures.append(f"artifact section missing: {section}")
    return (not failures, failures)


def resolve_task_input(task: EvalTask, base_dir: str | Path | None) -> str:
    """Rewrite a task's relative fixture reference to an absolute path.

    Eval tasks reference `fixtures/x.csv`, but the agent runs in a temp cwd, so
    a relative path won't resolve. Anchor it to the eval task directory.
    """
    if not task.dataset_fixture or base_dir is None:
        return task.input
    abs_fixture = (Path(base_dir) / task.dataset_fixture).resolve()
    return task.input.replace(task.dataset_fixture, str(abs_fixture))


def relevant_tasks(skill: Skill, tasks: list[EvalTask]) -> list[EvalTask]:
    """Tasks a candidate plausibly handles — keyword substring against input."""
    terms = [k.lower() for k in skill.keywords] + [skill.name.lower()]
    return [t for t in tasks if any(term and term in t.input.lower() for term in terms)]


def decide_promotion(
    pairs: list[tuple[EvalResult, EvalResult]], *, min_samples: int = MIN_SAMPLES
) -> tuple[str, dict[str, Any]]:
    """control vs treatment → promote / retire / needs_review.

    pairs: per task, (control_result without skill, treatment_result with skill).
    """
    n = len(pairs)
    if n < min_samples:
        return "needs_review", {"n": n, "reason": f"only {n} relevant tasks < {min_samples}"}
    t_pass = sum(1 for _, t in pairs if t.passed)
    c_pass = sum(1 for c, _ in pairs if c.passed)
    t_tools = statistics.mean(t.tool_call_count for _, t in pairs)
    c_tools = statistics.mean(c.tool_call_count for c, _ in pairs)
    metrics = {
        "n": n,
        "treatment_pass": t_pass,
        "control_pass": c_pass,
        "treatment_tools": round(t_tools, 2),
        "control_tools": round(c_tools, 2),
        "pass_rate": round(t_pass / n, 3),
    }
    # A skill that passes nothing is never promoted (covers the all-crashed case).
    if t_pass == 0:
        return "retire", metrics
    # Promote only on no regression: quality not worse AND cost not worse.
    if t_pass >= c_pass and t_tools <= c_tools:
        return "promote", metrics
    return "retire", metrics


class SkillEvaluator:
    """Evaluates candidate skills against the golden task set via an injected run_fn."""

    def __init__(
        self,
        eval_tasks_dir: str | Path | list[str | Path],
        skills_dir: str | Path,
        run_fn: RunFn,
        *,
        min_samples: int = MIN_SAMPLES,
    ) -> None:
        dirs = eval_tasks_dir if isinstance(eval_tasks_dir, (list, tuple)) else [eval_tasks_dir]
        self.tasks_dirs: list[Path] = [Path(d) for d in dirs]
        self.skills_dir = Path(skills_dir)
        self.run_fn = run_fn
        self.min_samples = min_samples

    def _all_tasks(self) -> list[EvalTask]:
        """Load + dedup tasks across all configured dirs (by task_id)."""
        tasks: list[EvalTask] = []
        seen: set[str] = set()
        for d in self.tasks_dirs:
            for t in load_eval_tasks(d):
                if t.task_id not in seen:
                    seen.add(t.task_id)
                    tasks.append(t)
        return tasks

    def evaluate(self, skill: Skill) -> dict[str, Any]:
        tasks = relevant_tasks(skill, self._all_tasks())
        pairs: list[tuple[EvalResult, EvalResult]] = []
        for task in tasks:
            control = self._run(task, None)
            treatment = self._run(task, skill)
            pairs.append((control, treatment))
        decision, metrics = decide_promotion(pairs, min_samples=self.min_samples)
        return {"skill": skill.name, "decision": decision, "metrics": metrics}

    def _run(self, task: EvalTask, skill: Skill | None) -> EvalResult:
        try:
            run = self.run_fn(task, skill)
        except Exception as e:
            # A crashing task must not kill the whole batch; record it as a
            # failed run so evaluation continues. Logged so infra failures
            # (auth/network) aren't silently miscounted as skill failures.
            logger.warning(
                "eval run_fn crashed on task %s (skill=%s): %r",
                task.task_id,
                skill.name if skill else None,
                e,
            )
            run = EvalRun(tool_call_count=0, has_error=True, final_text="")
        passed, failures = check_assertions(run, task.assertions)
        return EvalResult(task.task_id, passed, failures, run.tool_call_count)

    def apply(self, verdict: dict[str, Any]) -> Path | None:
        """Record the eval verdict on the skill file WITHOUT auto-promoting.

        Phase 1 governance (roadmap non-goal: no auto-promotion without human
        review): a ``promote`` verdict moves the skill to ``proposed_promote``
        (NOT ``active``) — it will not load into the live registry until a human
        runs ``evolution approve <name>``. ``retire`` (demotion) is safe to apply
        directly. ``needs_review`` leaves the file unchanged.
        """
        decision = verdict["decision"]
        if decision == "needs_review":
            return None
        for skill in load_skills(
            self.skills_dir, statuses=("candidate", "proposed_promote", "active")
        ):
            if skill.name != verdict["skill"]:
                continue
            skill.status = "proposed_promote" if decision == "promote" else "retired"
            skill.eval_score = verdict.get("metrics", {}).get("pass_rate")
            return save_skill(self.skills_dir, skill.to_dict())
        return None


# --- default run_fn: actually run the agent on a fixture (smoke level) --------


def eval_config_for(base: Any) -> Any:
    """Derive an eval-isolated config from a production one.

    Isolates eval from the runtime environment on every axis that could distort
    it: no kernel/memory/telemetry, AND permission decoupled — otherwise a
    `plan` mode or a `deny_patterns` from the env would block python_analysis/
    visualization/html_report and make every eval task fail. `deny_patterns=[]`
    is a fresh list, so it never shares identity with the base config.
    """
    from dataclasses import replace

    return replace(
        base,
        permission_mode="default",
        deny_patterns=[],
        persistent_kernel=False,
        enable_memory=False,
        enable_telemetry=False,
    )


def make_agent_run_fn(client: Any, *, allowed_paths: list[str | Path], config: Any = None) -> RunFn:
    """Build a run_fn that runs a task on the SAME agent production assembles.

    Goes through the composition root (AgentRuntime.from_config) so the eval
    agent has the production tool set — no more "eval ran a lighter agent"
    drift. Eval isolation is expressed by config switches (see eval_config_for),
    not by a hand-rolled separate assembly.
    """
    import asyncio

    from ..config import AgentConfig
    from ..events import CompleteEvent, ToolResultEvent
    from ..runtime import AgentRuntime

    eval_config = eval_config_for(config or AgentConfig.from_env())

    def run(task: EvalTask, skill: Skill | None) -> EvalRun:
        runtime = AgentRuntime.from_config(
            eval_config,
            client=client,
            extra_skills=[skill] if skill is not None else (),
            analysis_paths=allowed_paths,
        )
        effective_input = resolve_task_input(task, allowed_paths[0] if allowed_paths else None)

        async def go() -> EvalRun:
            tool_calls = 0
            has_error = False
            final = ""
            tools_used: list[str] = []
            artifact_paths: list[str] = []
            try:
                async for event in runtime.loop.run(effective_input):
                    if isinstance(event, ToolResultEvent):
                        tool_calls += 1
                        has_error = has_error or event.is_error
                        if event.tool_name:
                            tools_used.append(event.tool_name)
                        artifact_paths.extend(event.artifacts)
                    elif isinstance(event, CompleteEvent):
                        final = event.final_text
                # Wave 7.5: extract HTML section markers from first artifact
                artifact_sections: list[str] = []
                if artifact_paths:
                    try:
                        html_text = Path(artifact_paths[0]).read_text(encoding="utf-8")
                        if 'class="card summary"' in html_text:
                            artifact_sections.append("executive_summary")
                        if 'class="card caveat"' in html_text:
                            artifact_sections.append("caveat")
                        if 'class="card recommendation"' in html_text:
                            artifact_sections.append("recommendation")
                        if 'class="card finding"' in html_text:
                            artifact_sections.append("finding")
                        if 'class="card chart-block"' in html_text:
                            artifact_sections.append("chart")
                        if 'class="kpi-strip"' in html_text:
                            artifact_sections.append("kpi")
                    except (OSError, IndexError):
                        pass
                return EvalRun(
                    tool_calls,
                    has_error,
                    final,
                    tuple(tools_used),
                    tuple(artifact_paths),
                    tuple(artifact_sections),
                )
            finally:
                await runtime.shutdown()  # release the per-task runtime (kernel, etc.)

        return asyncio.run(go())

    return run


def register_evaluate_cli(subparsers: Any) -> None:
    """Register the ``evaluate`` / ``approve`` / ``retire`` subcommands.

    ``evaluate`` only PROPOSES promotion (status=proposed_promote); ``approve``
    is the human-in-the-loop gate that writes active; ``retire`` demotes.
    """
    p_eval = subparsers.add_parser(
        "evaluate", help="fixture 重跑评估候选技能(promote → proposed_promote,不自动激活)"
    )
    p_eval.set_defaults(func=_cmd_evaluate)

    p_approve = subparsers.add_parser(
        "approve", help="人工批准 proposed_promote/candidate → active(唯一激活入口)"
    )
    p_approve.add_argument("name", help="技能名")
    p_approve.set_defaults(func=_cmd_approve)

    p_retire = subparsers.add_parser("retire", help="退役技能 → retired(移出活注册表)")
    p_retire.add_argument("name", help="技能名")
    p_retire.set_defaults(func=_cmd_retire)


def _cmd_evaluate(args: Any) -> int:
    from ..config import AgentConfig
    from ..protocol.client import AnthropicApiClient

    config = AgentConfig.from_env()
    if not config.api_key:
        print("ANTHROPIC_API_KEY not set; evaluation reruns the agent.")
        return 1
    eval_dir = Path(__file__).resolve().parent.parent.parent.parent / "examples" / "eval_tasks"
    client = AnthropicApiClient(api_key=config.api_key, model=config.model)
    run_fn = make_agent_run_fn(client, allowed_paths=[eval_dir], config=config)
    evaluator = SkillEvaluator([eval_dir, config.eval_tasks_dir()], config.skills_dir(), run_fn)

    candidates = load_skills(config.skills_dir(), statuses=("candidate",))
    if not candidates:
        print("没有待评估的 candidate 技能。")
        return 0
    for skill in candidates:
        verdict = evaluator.evaluate(skill)
        print(f"[{verdict['skill']}] → {verdict['decision']}  {verdict['metrics']}")
        applied = evaluator.apply(verdict)
        if applied is not None and verdict["decision"] == "promote":
            print(
                f"  已标记 proposed_promote(未自动激活):{applied}\n"
                f"  人工确认后运行 `evolution approve {verdict['skill']}` 才会进入活注册表。"
            )
        elif applied is not None:
            print(f"  已退役(retired):{applied}")
        elif verdict["decision"] == "needs_review":
            print("  样本不足,降级为人审清单(保持 candidate)。")
    return 0


def approve_skill(skills_dir: str | Path, name: str) -> int:
    """Human-in-the-loop gate: the ONLY path that writes status=active.

    Returns 0 on success (already-active is a no-op success), 1 if not found.
    """
    for skill in load_skills(skills_dir, statuses=("candidate", "proposed_promote", "active")):
        if skill.name == name:
            if skill.status == "active":
                return 0
            skill.status = "active"
            save_skill(skills_dir, skill.to_dict())
            return 0
    return 1


def retire_skill(skills_dir: str | Path, name: str) -> int:
    """Manually retire a skill (demote to retired; removed from the live registry).

    Returns 0 on success (already-retired is a no-op success), 1 if not found.
    """
    for skill in load_skills(
        skills_dir, statuses=("candidate", "proposed_promote", "active", "retired")
    ):
        if skill.name == name:
            if skill.status == "retired":
                return 0
            skill.status = "retired"
            save_skill(skills_dir, skill.to_dict())
            return 0
    return 1


def _cmd_approve(args: Any) -> int:
    """Human-in-the-loop gate: the ONLY path that writes status=active."""
    from ..config import AgentConfig

    return approve_skill(AgentConfig.from_env().skills_dir(), args.name)


def _cmd_retire(args: Any) -> int:
    """Manually retire a skill (demote to retired; removed from the live registry)."""
    from ..config import AgentConfig

    return retire_skill(AgentConfig.from_env().skills_dir(), args.name)


__all__ = [
    "EvalResult",
    "EvalRun",
    "EvalTask",
    "SkillEvaluator",
    "check_assertions",
    "decide_promotion",
    "load_eval_tasks",
    "make_agent_run_fn",
    "register_evaluate_cli",
    "relevant_tasks",
]
