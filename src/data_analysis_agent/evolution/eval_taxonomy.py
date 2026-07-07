"""eval 断言失败分类学(Wave 7):区分 code/tool 失败 vs 报告质量失败。

spec §8 Wave 7 acceptance #2:'Eval output separates code/tool failures from
report-quality failures'。把 ``check_assertions`` 产的失败串按前缀归入三桶,
让 eval 输出能区分"代码/工具层失败"与"报告质量层失败"。
"""

from __future__ import annotations

__all__ = ["classify_failures", "CODE_TOOL", "REPORT_QUALITY", "OTHER"]

CODE_TOOL = "code_tool"
REPORT_QUALITY = "report_quality"
OTHER = "other"

# evaluator.check_assertions 产的失败串前缀 → 桶
_CODE_TOOL_MARKERS = (
    "a tool result was an error",
    "tool_call_count",
)
_REPORT_QUALITY_MARKERS = (
    "final text missing",
    "final text did not match",
    "required tool missing",
    "no artifact produced",
)


def classify_failures(failures: list[str]) -> dict[str, list[str]]:
    """按失败信息归类到 code_tool / report_quality / other 三桶。"""
    buckets: dict[str, list[str]] = {CODE_TOOL: [], REPORT_QUALITY: [], OTHER: []}
    for f in failures:
        if any(m in f for m in _CODE_TOOL_MARKERS):
            buckets[CODE_TOOL].append(f)
        elif any(m in f for m in _REPORT_QUALITY_MARKERS):
            buckets[REPORT_QUALITY].append(f)
        else:
            buckets[OTHER].append(f)
    return buckets
