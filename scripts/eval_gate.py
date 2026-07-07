"""报告 eval gate(Wave 7):确定性结构校验,不跑 LLM。

校验 examples/eval_tasks 下 *.json:
- schema:task_id + input + assertions(dict)
- 数量:≥ --min-tasks(默认 20)
- 域覆盖:input 跨 ≥ --min-domains 个域关键词
- 方法非数值(ADR 0005):assertions 键必须在白名单内(键白名单,非 regex——
  ``tool_call_count_max: 12`` 是结构上限,合法)

spec §8 Wave 7:'Keep the eval gate optional until runtime cost and determinism
are controlled.' → 本脚本只做确定性结构校验,实跑 eval 沿用 evolution evaluate CLI。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ADR 0005:允许的断言键(全 method/structure-only)。键白名单 = 拒绝任何数值等式断言。
_ALLOWED_ASSERTION_KEYS = frozenset(
    {
        "no_error_results",
        "min_tool_calls",
        "tool_call_count_max",
        "final_text_contains",
        "final_text_regex",
        "required_tools",
        "artifact_produced",
    }
)

# 域关键词(CJK + en)
_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "retail": ("零售", "零售业", "retail"),
    "marketing": ("营销", "市场营销", "marketing"),
    "saas": ("SaaS", "订阅", "saas", "subscription"),
    "finance": ("金融", "财务", "finance"),
    "operations": ("运营", "operations"),
    "risk": ("风险", "risk"),
}

MIN_TASKS = 20
MIN_DOMAINS = 3

# ADR 0005 值级守卫:白名单键内的 final_text_contains/regex 值若含比较运算符+数字
# (==/>=/<=/!= 后跟数字)即数值钉,拒绝。裸数字/百分号/万 留作者自律(假阳性风险)。
_NUMERIC_PIN_RE = re.compile(r"[<>=!]=\s*\d")


def _scan_value_pins(assertions: dict) -> list[str]:
    """扫描白名单键内的值是否钉了数值(ADR 0005 值级补充)。"""
    pins: list[str] = []
    for key in ("final_text_contains", "final_text_regex"):
        val = assertions.get(key)
        if isinstance(val, str):
            val = [val]
        if not isinstance(val, list):
            continue
        for v in val:
            if isinstance(v, str) and _NUMERIC_PIN_RE.search(v):
                pins.append(f"{key} value pins a number (ADR 0005): {v!r}")
    return pins


def validate_task(rec: dict) -> list[str]:
    """校验单个 eval 任务文件的 schema + ADR 0005 键白名单。"""
    errors: list[str] = []
    if not rec.get("task_id"):
        errors.append("missing task_id")
    if not rec.get("input"):
        errors.append("missing input")
    assertions = rec.get("assertions")
    if not isinstance(assertions, dict):
        errors.append("assertions must be an object")
        return errors
    for key in assertions:  # ADR 0005:键白名单(拒绝数值等式断言)
        if key not in _ALLOWED_ASSERTION_KEYS:
            errors.append(f"non-whitelisted assertion key (ADR 0005): {key}")
    errors.extend(_scan_value_pins(assertions))  # ADR 0005 值级:比较运算符+数字
    return errors


def _domain_of(input_text: str) -> str | None:
    lower = input_text.lower()
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw.lower() in lower for kw in keywords):
            return domain
    return None


def run_gate(
    tasks_dir: str | Path,
    *,
    min_tasks: int = MIN_TASKS,
    min_domains: int = MIN_DOMAINS,
) -> tuple[bool, list[str]]:
    """确定性校验目录下所有 *.json。返 (ok, errors)。"""
    d = Path(tasks_dir)
    files = sorted(d.rglob("*.json")) if d.exists() else []
    errors: list[str] = []
    task_count = 0
    domains: set[str] = set()
    for path in files:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{path.name}: invalid JSON ({exc})")
            continue
        if not isinstance(rec, dict):
            errors.append(f"{path.name}: not an object")
            continue
        for e in validate_task(rec):
            errors.append(f"{path.name}: {e}")
        if rec.get("task_id") and rec.get("input"):
            task_count += 1
            dom = _domain_of(str(rec.get("input", "")))
            if dom:
                domains.add(dom)
    if task_count < min_tasks:
        errors.append(f"too few tasks: {task_count} < {min_tasks}")
    if len(domains) < min_domains:
        errors.append(f"too few domains: {len(domains)} < {min_domains} (found: {sorted(domains)})")
    return (not errors, errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="报告 eval gate(确定性结构校验,不跑 LLM)")
    parser.add_argument("kind", nargs="?", default="report", help="gate kind(目前仅 report)")
    parser.add_argument("dir", nargs="?", default="examples/eval_tasks", help="eval tasks 目录")
    parser.add_argument("--min-tasks", type=int, default=MIN_TASKS)
    parser.add_argument("--min-domains", type=int, default=MIN_DOMAINS)
    args = parser.parse_args()
    ok, errors = run_gate(args.dir, min_tasks=args.min_tasks, min_domains=args.min_domains)
    if ok:
        print(f"PASS — eval gate {args.kind} ({args.dir})")
        return 0
    print(f"FAIL — eval gate {args.kind} ({args.dir}):")
    for e in errors:
        print(f"  - {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
