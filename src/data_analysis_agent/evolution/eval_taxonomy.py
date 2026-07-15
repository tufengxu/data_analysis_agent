"""eval 断言失败分类学(Wave 7):区分 code/tool 失败 vs 报告质量失败 vs 数值正确性失败。

spec §8 Wave 7 acceptance #2:'Eval output separates code/tool failures from
report-quality failures'。把 ``check_assertions`` 产的失败串按前缀归入四桶,
让 eval 输出能区分"代码/工具层失败"、"报告质量层失败"与"数值正确性失败"
(Wave 8:跑通但算错数——numeric_anchor 未命中)。
"""

from __future__ import annotations

__all__ = ["classify_failures", "CODE_TOOL", "REPORT_QUALITY", "CORRECTNESS", "OTHER"]

CODE_TOOL = "code_tool"
REPORT_QUALITY = "report_quality"
CORRECTNESS = "correctness"  # Wave 8: numeric-anchor (frozen-fixture value) failures
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
    "artifact section missing",
)
# Wave 8: a numeric anchor miss means the agent ran clean but computed the wrong
# number — a correctness failure distinct from code/tool breakage or report format.
# Matches all numeric-anchor failure prefixes ("... not found" / "... malformed" /
# "... entry must be an object").
_CORRECTNESS_MARKERS = ("numeric anchor",)


def classify_failures(failures: list[str]) -> dict[str, list[str]]:
    """按失败信息归类到 code_tool / report_quality / correctness / other 四桶。"""
    buckets: dict[str, list[str]] = {
        CODE_TOOL: [],
        REPORT_QUALITY: [],
        CORRECTNESS: [],
        OTHER: [],
    }
    for f in failures:
        if any(m in f for m in _CODE_TOOL_MARKERS):
            buckets[CODE_TOOL].append(f)
        elif any(m in f for m in _REPORT_QUALITY_MARKERS):
            buckets[REPORT_QUALITY].append(f)
        elif any(m in f for m in _CORRECTNESS_MARKERS):
            buckets[CORRECTNESS].append(f)
        else:
            buckets[OTHER].append(f)
    return buckets
