"""Wave 7: 报告 eval 断言(required_tools/artifact_produced)+ 失败分类学 + eval_gate 结构校验。"""

from __future__ import annotations

import json
from pathlib import Path

from eval_gate import run_gate, validate_task

from data_analysis_agent.evolution import eval_taxonomy
from data_analysis_agent.evolution.evaluator import (
    EvalRun,
    _capture_python_analysis,
    check_assertions,
)

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


def test_classify_correctness():
    """Wave 8: numeric-anchor 失败(跑通但算错数)归入 CORRECTNESS 桶,不污染 OTHER。"""
    buckets = eval_taxonomy.classify_failures(
        [
            "numeric anchor not found: no parsed value within tolerance 0.001 of 5000",
            "numeric anchor malformed (value must be a number): ...",
        ]
    )
    assert len(buckets[eval_taxonomy.CORRECTNESS]) == 2
    assert not buckets[eval_taxonomy.OTHER]
    assert not buckets[eval_taxonomy.CODE_TOOL]
    assert not buckets[eval_taxonomy.REPORT_QUALITY]


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


# ----------------------------- Wave 7.5: section 级 HTML 校验 -----------------------------


def test_artifact_has_sections_pass():
    ok, _ = check_assertions(
        _run(artifact_sections=("executive_summary", "caveat")),
        {"artifact_has_sections": ["executive_summary"]},
    )
    assert ok


def test_artifact_has_sections_fail_exact_prefix():
    ok, fails = check_assertions(
        _run(artifact_sections=()),
        {"artifact_has_sections": ["executive_summary", "caveat"]},
    )
    assert not ok
    assert any(f.startswith("artifact section missing: executive_summary") for f in fails)
    assert any(f.startswith("artifact section missing: caveat") for f in fails)


def test_eval_run_artifact_sections_default():
    run = EvalRun(tool_call_count=0, has_error=False, final_text="")
    assert run.artifact_sections == ()


def test_eval_gate_allows_artifact_has_sections():
    errs = validate_task(
        {
            "task_id": "x",
            "input": "y",
            "assertions": {"artifact_has_sections": ["executive_summary", "caveat"]},
        }
    )
    assert errs == []


def test_classify_artifact_section_report_quality():
    buckets = eval_taxonomy.classify_failures(["artifact section missing: executive_summary"])
    assert len(buckets[eval_taxonomy.REPORT_QUALITY]) == 1


# ----------------------------- Wave 8: 冻结 fixture 数值锚 -----------------------------


def test_eval_run_computed_outputs_default():
    run = EvalRun(tool_call_count=0, has_error=False, final_text="")
    assert run.computed_outputs == ()


def test_numeric_anchor_pass_when_present():
    """computed_outputs 含锚定值 → 过(check_assertions 解析 python_analysis 输出)。"""
    ok, fails = check_assertions(
        _run(computed_outputs=("revenue 总额 = 5000",)),
        {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]},
    )
    assert ok and not fails


def test_numeric_anchor_fail_wrong_number():
    """confidently-wrong:输出 4800(真实 5000)→ 锚失败(TR-2 正是为此而设)。"""
    ok, fails = check_assertions(
        _run(computed_outputs=("revenue 总额 = 4800",)),
        {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]},
    )
    assert not ok
    assert any(f.startswith("numeric anchor not found") for f in fails)


def test_numeric_anchor_fail_missing():
    """无 python_analysis 输出(空 computed_outputs)→ 无可解析值 → 锚失败。"""
    ok, fails = check_assertions(
        _run(computed_outputs=()),
        {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]},
    )
    assert not ok
    assert any(f.startswith("numeric anchor not found") for f in fails)


def test_numeric_anchor_tolerance_window():
    """window = abs(value)*tolerance = 5000*0.001 = 5 → 闭区间 [4995, 5005]。"""
    base = {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]}
    ok_in, _ = check_assertions(_run(computed_outputs=("合计 4996",)), base)
    assert ok_in  # 4996 在窗内
    ok_edge, _ = check_assertions(_run(computed_outputs=("合计 5005",)), base)
    assert ok_edge  # 闭区间边界命中
    ok_out, _ = check_assertions(_run(computed_outputs=("合计 4994",)), base)
    assert not ok_out  # 4994 在窗外


def test_numeric_anchor_value_near_zero():
    """value≈0 守卫:相对窗口塌缩为 0,用绝对地板 1e-9 使 '0.0' 仍可命中。"""
    base = {"numeric_anchor": [{"value": 0, "tolerance": 0.0}]}
    ok_zero, _ = check_assertions(_run(computed_outputs=("差值 0.0",)), base)
    assert ok_zero
    fail_nonzero, _ = check_assertions(_run(computed_outputs=("差值 0.5",)), base)
    assert not fail_nonzero


def test_numeric_anchor_bare_dict_coerced():
    """裸 dict(非 list)被 coerce 成单元素列表,不静默忽略(同 final_text_contains 惯例)。"""
    ok, _ = check_assertions(
        _run(computed_outputs=("5000",)),
        {"numeric_anchor": {"value": 5000, "tolerance": 0.001}},
    )
    assert ok


def test_numeric_anchor_label_in_failure():
    """失败信息带上 label,便于 eval 输出定位是哪个锚没命中。"""
    ok, fails = check_assertions(
        _run(computed_outputs=()),
        {"numeric_anchor": [{"value": 5000, "tolerance": 0.001, "label": "revenue 总额"}]},
    )
    assert not ok
    assert any("revenue 总额" in f for f in fails)


def test_numeric_anchor_multi_output_concatenated():
    """多次 python_analysis 输出被拼接扫描;只要任一含锚定值即命中。"""
    ok, _ = check_assertions(
        _run(computed_outputs=("均值 1000", "总额 5000")),
        {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]},
    )
    assert ok


def test_numeric_anchor_malformed_anchor_reports_failure():
    """check_assertions 对畸形 anchor(非数 value)报失败,而非抛异常崩掉整轮。"""
    ok, fails = check_assertions(
        _run(computed_outputs=("5000",)),
        {"numeric_anchor": [{"value": "not-a-number", "tolerance": 0.001}]},
    )
    assert not ok
    assert any("malformed" in f for f in fails)


def test_numeric_anchor_sign_error_caught():
    """符号错被捕获:输出 -5000(真实 5000)不命中锚 5000(正是该 feature 的目的)。"""
    base = {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]}
    ok_pos, _ = check_assertions(_run(computed_outputs=("结果 5000",)), base)
    assert ok_pos
    ok_neg, fails = check_assertions(_run(computed_outputs=("结果 -5000",)), base)
    assert not ok_neg
    assert any(f.startswith("numeric anchor not found") for f in fails)


def test_numeric_anchor_range_no_spurious_negative():
    """区间 '5000-10000' 不产生伪 -10000(连字符不绑定为负号,lookbehind 生效)。"""
    ok, _ = check_assertions(
        _run(computed_outputs=("区间 5000-10000",)),
        {"numeric_anchor": [{"value": -10000, "tolerance": 0.001}]},
    )
    assert not ok  # 不应把 10000 当成 -10000 命中


def test_numeric_anchor_cjk_no_space_parses():
    """CJK 紧贴数字(无空格)'总额5000' 仍解析出 5000(bare 数字不被 lookbehind 拦)。"""
    ok, _ = check_assertions(
        _run(computed_outputs=("总额5000",)),
        {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]},
    )
    assert ok


# ----------------------------- _capture_python_analysis(Part 2) -----------------------------


def test_capture_python_analysis_success():
    assert _capture_python_analysis("python_analysis", "总营收 5000", False) == "总营收 5000"


def test_capture_python_analysis_skips_error_result():
    """python_analysis 的错误结果不是计算值 → 不捕获。"""
    assert _capture_python_analysis("python_analysis", "Traceback ...", True) is None


def test_capture_python_analysis_skips_other_tools():
    assert _capture_python_analysis("data_profile", "schema...", False) is None
    assert _capture_python_analysis("html_report", "<html>", False) is None


def test_capture_python_analysis_skips_empty():
    assert _capture_python_analysis("python_analysis", "", False) is None


def test_capture_python_analysis_caps_long_content():
    big = "9" * 30_000
    captured = _capture_python_analysis("python_analysis", big, False)
    assert captured is not None and len(captured) == 20_000


# ----------------------------- eval_gate: numeric_anchor 纪律 -----------------------------


def test_eval_gate_numeric_anchor_with_fixture_ok():
    """numeric_anchor + dataset_fixture → 合法(ADR 0005 例外)。"""
    errs = validate_task(
        {
            "task_id": "x",
            "input": "y",
            "dataset_fixture": "fixtures/revenue.csv",
            "assertions": {
                "numeric_anchor": [{"value": 5000, "tolerance": 0.001, "label": "总额"}]
            },
        }
    )
    assert errs == []


def test_eval_gate_numeric_anchor_without_fixture_rejected():
    """numeric_anchor 无 dataset_fixture → 拒(ADR 0005:只有冻结数据可锚数值)。"""
    errs = validate_task(
        {
            "task_id": "x",
            "input": "y",
            "assertions": {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]},
        }
    )
    assert any("requires" in e and "dataset_fixture" in e for e in errs)


def test_eval_gate_numeric_anchor_nonstring_fixture_rejected():
    """dataset_fixture 非字符串(truthy 但非 str,如 true/123)→ 拒(m4)。"""
    errs = validate_task(
        {
            "task_id": "x",
            "input": "y",
            "dataset_fixture": True,
            "assertions": {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]},
        }
    )
    assert any("requires" in e and "dataset_fixture" in e for e in errs)


def test_eval_gate_numeric_anchor_malformed_rejected():
    """结构错误:value/tolerance 非数、tolerance 负、entry 非 object → 拒。"""
    errs = validate_task(
        {
            "task_id": "x",
            "input": "y",
            "dataset_fixture": "fixtures/revenue.csv",
            "assertions": {
                "numeric_anchor": [
                    {"value": "5000", "tolerance": 0.001},  # value 非数
                    {"value": 5000, "tolerance": -0.1},  # tolerance 负
                    "not-an-object",  # entry 非 object
                ]
            },
        }
    )
    assert any("value must be a number" in e for e in errs)
    assert any("tolerance must be a non-negative" in e for e in errs)
    assert any("must be an object" in e for e in errs)


def test_eval_gate_numeric_anchor_not_value_pinned():
    """numeric_anchor 的 value 字段不被 _NUMERIC_PIN_RE 误判(设计 Part 4)。"""
    errs = validate_task(
        {
            "task_id": "x",
            "input": "y",
            "dataset_fixture": "fixtures/revenue.csv",
            "assertions": {"numeric_anchor": [{"value": 5000, "tolerance": 0.001}]},
        }
    )
    assert not any("pins a number" in e for e in errs)


def test_eval_gate_sample_numeric_anchor_task_validates():
    """落盘的样本任务过 validate_task(端到端结构校验)。"""
    sample = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "eval_tasks"
        / "numeric_anchor_revenue_sum.json"
    )
    rec = json.loads(sample.read_text(encoding="utf-8"))
    assert validate_task(rec) == []
