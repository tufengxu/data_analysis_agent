"""ReportContextTool: 从 data_profile + 工具事件构建报告上下文(只读)。

薄封装 ``reporting.context_collector.build_data_context`` +
``build_process_context``。无 I/O、无 LLM。
"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.reporting.context_collector import (
    build_data_context,
    build_process_context,
)
from data_analysis_agent.reporting.model import DataContext, ProcessContext

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class ReportContextTool(Tool):
    """Collect Data Context + Process Context for a report (read-only)."""

    @property
    def name(self) -> str:
        return "report_context"

    @property
    def description(self) -> str:
        return (
            "Collect Data Context (from a data_profile result object) and Process Context "
            "(from summarized tool-event objects) into structured reporting context: "
            "candidate date/metric/dimension columns, business grain, tool steps, assumptions. "
            "Pass sensitive_mode=true to drop process detail for privacy. Read-only; use "
            "before and after analysis."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "object",
                    "description": "The data_profile tool's output object (or its 'profile' metadata).",
                },
                "events": {
                    "type": "array",
                    "description": "Optional summarized tool-event objects: {step_id, tool, summary, ...}.",
                },
                "sensitive_mode": {"type": "boolean"},
            },
            "required": ["profile"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        profile = input_data.get("profile")
        if not isinstance(profile, dict):
            return ValidationResult.fail(
                "profile is required and must be an object (data_profile output)"
            )
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        profile = input_data["profile"]
        events_raw = input_data.get("events")
        events = events_raw if isinstance(events_raw, list) else []
        sensitive = input_data.get("sensitive_mode") is True
        data_context = build_data_context(profile)
        process_context = build_process_context(events, sensitive_mode=sensitive)
        return ToolResult(
            content=_render(data_context, process_context),
            metadata={
                "data_context": data_context.to_dict(),
                "process_context": process_context.to_dict(),
            },
        )


def _render(data_context: DataContext, process_context: ProcessContext) -> str:
    lines = ["data_context:"]
    if data_context.tables:
        lines.append(f"  tables: {len(data_context.tables)}")
    if data_context.candidate_date_columns:
        lines.append(f"  candidate_date_columns: {', '.join(data_context.candidate_date_columns)}")
    if data_context.candidate_metric_columns:
        lines.append(
            f"  candidate_metric_columns: {', '.join(data_context.candidate_metric_columns)}"
        )
    if data_context.candidate_dimensions:
        lines.append(f"  candidate_dimensions: {', '.join(data_context.candidate_dimensions)}")
    if data_context.business_grain:
        lines.append(f"  business_grain: {data_context.business_grain}")
    if data_context.data_gaps:
        lines.append(f"  data_gaps: {', '.join(data_context.data_gaps)}")
    lines.append("process_context:")
    if process_context.sensitive_mode:
        lines.append("  sensitive_mode: True (steps dropped)")
    else:
        lines.append(f"  steps: {len(process_context.steps)}")
        if process_context.rejected_paths:
            lines.append(f"  rejected_paths: {len(process_context.rejected_paths)}")
    return "\n".join(lines)
