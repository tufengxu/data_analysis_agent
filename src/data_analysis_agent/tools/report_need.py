"""ReportNeedTool: 把原始报告请求解析为 UserNeed(只读)。

薄封装 ``reporting.requirement_parser.parse_user_need`` —— 把 Wave 1-2 的领域层
暴露给模型与 harness。无 I/O、无 LLM、无副作用。
"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.reporting.model import UserNeed
from data_analysis_agent.reporting.requirement_parser import parse_user_need

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class ReportNeedTool(Tool):
    """Parse a raw report request into a UserNeed (explicit/implicit + uncertainties)."""

    @property
    def name(self) -> str:
        return "report_need"

    @property
    def description(self) -> str:
        return (
            "Parse a raw report request into a UserNeed: separates EXPLICIT requirements "
            "(lexical facts: requested outputs, audience, language) from IMPLICIT inferences "
            "(likely report type, cadence, narrative style), and lists uncertainties + whether "
            "a clarification is needed. Read-only; use before report_contract."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "raw_request": {
                    "type": "string",
                    "description": "The user's raw report request (natural language).",
                },
            },
            "required": ["raw_request"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        raw = input_data.get("raw_request")
        if not isinstance(raw, str) or not raw.strip():
            return ValidationResult.fail("raw_request is required and must be a non-empty string")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        need = parse_user_need(input_data["raw_request"])
        return ToolResult(content=_render(need), metadata={"user_need": need.to_dict()})


def _render(need: UserNeed) -> str:
    ex = need.explicit_requirements
    im = need.implicit_requirements
    lines = [f"raw_request: {need.raw_request}", "explicit:"]
    if ex.language:
        lines.append(f"  language: {ex.language}")
    if ex.requested_outputs:
        lines.append(f"  requested_outputs: {', '.join(ex.requested_outputs)}")
    if ex.audience:
        lines.append(f"  audience: {ex.audience}")
    if not (ex.language or ex.requested_outputs or ex.audience):
        lines.append("  (none lexically detectable)")
    lines.append("implicit:")
    if im.likely_report_type:
        lines.append(f"  likely_report_type: {im.likely_report_type}")
    if im.cadence:
        lines.append(f"  cadence: {im.cadence}")
    if im.narrative_style:
        lines.append(f"  narrative_style: {im.narrative_style}")
    if not (im.likely_report_type or im.cadence or im.narrative_style):
        lines.append("  (none inferred)")
    if need.uncertainties:
        lines.append("uncertainties:")
        for u in need.uncertainties:
            flag = " [needs clarification]" if u.needs_clarification else ""
            lines.append(f"  - {u.topic}: {u.why}{flag}")
    if need.clarification_needed:
        lines.append("clarification_needed: True")
    return "\n".join(lines)
