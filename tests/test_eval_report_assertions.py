"""Wave 7: 报告 eval 断言(required_tools/artifact_produced)+ 失败分类学 + eval_gate 结构校验。"""

from __future__ import annotations

import json
from pathlib import Path

from eval_gate import run_gate, validate_task

from data_analysis_agent.evolution import eval_taxonomy
from data_analysis_agent.evolution.evaluator import EvalRun, check_assertions

# ----------------------------- check_assertions 新断言 -----------------------------


def _run(**kw) -> EvalRun:
    base = {"tool_call_count": 3, "has_error": False, "final_text": "报告已生成"}
    base.update(kw)
    return EvalRun(**base)


def test_required_tools_pass():
    ok, fails = check_assertions(
        _run(tools_used=("data_profile", "html_report")), {"required_tools": ["html_report"]}
    )
    assert ok and not fails


def test_required_tools_fail_exact_prefix():
    ok, fails = check_assertions(
        _run(tools_used=("data_profile",)), {"required_tools": ["html_report"]}
    )
    assert not ok
    assert any(f.startswith("required tool missing: html_report") for f in fails)


def test_required_tools_bare_string_coerced():
    """裸字符串 'html_report'(非 list)被 coerce 成 list 检查,不静默忽略。"""
    ok, _ = check_assertions(_run(tools_used=("html_report",)), {"required_tools": "html_report"})
    assert ok
    ok2, fails2 = check_assertions(
        _run(tools_used=("data_profile",)), {"required_tools": "html_report"}
    )
    assert not ok2 and any("required tool missing" in f for f in fails2)


def test_artifact_produced_pass():
    ok, _ = check_assertions(
        _run(artifact_paths=("/tmp/report.html",)), {"artifact_produced": True}
    )
    assert ok


def test_artifact_produced_fail_exact_prefix():
    ok, fails = check_assertions(_run(artifact_paths=()), {"artifact_produced": True})
    assert not ok
    assert "no artifact produced" in fails


def test_eval_run_new_fields_default_empty():
    run = EvalRun(tool_call_count=0, has_error=False, final_text="")
    assert run.tools_used == ()
    assert run.artifact_paths == ()


def test_old_task_without_new_assertions_still_passes():
    """向后兼容:仅 {no_error_results: true} 的旧任务过 check_assertions。"""
    ok, fails = check_assertions(_run(), {"no_error_results": True})
    assert ok and not fails


# ----------------------------- 失败分类学 -----------------------------


def test_classify_code_tool():
    buckets = eval_taxonomy.classify_failures(
        ["a tool result was an error", "tool_call_count 15 > 12"]
    )
    assert len(buckets[eval_taxonomy.CODE_TOOL]) == 2
    assert not buckets[eval_taxonomy.REPORT_QUALITY]


def test_classify_report_quality():
    buckets = eval_taxonomy.classify_failures(
        [
            "final text missing: 报告",
            "final text did not match /foo/",
            "required tool missing: html_report",
            "no artifact produced",
        ]
    )
    assert len(buckets[eval_taxonomy.REPORT_QUALITY]) == 4
    assert not buckets[eval_taxonomy.CODE_TOOL]


def test_classify_other():
    buckets = eval_taxonomy.classify_failures(["something unexpected"])
    assert len(buckets[eval_taxonomy.OTHER]) == 1


# ----------------------------- eval_gate 结构校验 -----------------------------


def test_validate_task_schema():
    assert validate_task({"task_id": "x", "input": "y", "assertions": {}}) == []
    assert validate_task({})  # 缺 task_id/input
    assert validate_task({"task_id": "x", "input": "y"})  # 缺 assertions


def test_eval_gate_allows_structural_caps():
    """ADR 0005:min_tool_calls/tool_call_count_max 是结构上限,合法(不被数值扫描误判)。"""
    errs = validate_task(
        {
            "task_id": "x",
            "input": "y",
            "assertions": {"min_tool_calls": 2, "tool_call_count_max": 12},
        }
    )
    assert errs == []


def test_eval_gate_rejects_non_whitelisted_key():
    """非白名单键(如 pass_rate/GMV 数值断言)→ 拒(ADR 0005)。"""
    errs = validate_task({"task_id": "x", "input": "y", "assertions": {"pass_rate_gte": 0.9}})
    assert any("non-whitelisted" in e for e in errs)


def test_eval_gate_rejects_numeric_value_pin():
    """ADR 0005 值级:白名单键内的比较运算符+数字(如 'GMV >= 12万')→ 拒。"""
    errs = validate_task(
        {
            "task_id": "x",
            "input": "y",
            "assertions": {"final_text_contains": ["GMV >= 12万", "留存率 == 12%"]},
        }
    )
    assert any("pins a number" in e for e in errs)


def test_eval_gate_allows_benign_digit_in_contains():
    """裸数字(无比较运算符)不在值级扫描范围(留作者自律,避免假阳性)。"""
    errs = validate_task(
        {"task_id": "x", "input": "y", "assertions": {"final_text_contains": ["TOP 3 渠道"]}}
    )
    assert errs == []


def _write_task(path: Path, task_id: str, input_text: str, assertions: dict | None = None) -> None:
    rec = {"task_id": task_id, "input": input_text, "assertions": assertions or {}}
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def test_run_gate_pass(tmp_path: Path):
    d = tmp_path / "reports"
    d.mkdir()
    _write_task(d / "a.json", "t1", "零售日报", {"required_tools": ["html_report"]})
    _write_task(d / "b.json", "t2", "营销周报", {})
    _write_task(d / "c.json", "t3", "订阅(SaaS)复盘", {})
    ok, errors = run_gate(d, min_tasks=3, min_domains=3)
    assert ok, errors


def test_run_gate_too_few_tasks(tmp_path: Path):
    d = tmp_path / "r"
    d.mkdir()
    _write_task(d / "a.json", "t1", "零售")
    ok, errors = run_gate(d, min_tasks=20, min_domains=1)
    assert not ok
    assert any("too few tasks" in e for e in errors)


def test_run_gate_too_few_domains(tmp_path: Path):
    d = tmp_path / "r"
    d.mkdir()
    for i in range(5):
        _write_task(d / f"{i}.json", f"t{i}", "零售日报")
    ok, errors = run_gate(d, min_tasks=5, min_domains=3)
    assert not ok
    assert any("too few domains" in e for e in errors)


def test_eval_gate_descriptive_smoke_validates():
    """既有 descriptive_smoke.json(无 required_tools)在 eval_gate 下仍合法(向后兼容)。"""
    ok, errors = run_gate(
        Path(__file__).resolve().parent.parent / "examples" / "eval_tasks",
        min_tasks=20,
        min_domains=3,
    )
    assert ok, errors
