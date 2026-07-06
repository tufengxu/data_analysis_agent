"""ReportContractTool: 把 UserNeed+上下文 归一化为 ReportContract(只读)。

薄封装 Wave 1-2 ``reporting``:用 ``traceability.link_to_contract_fields`` 产出的
TraceLink 填充 ``field_sources`` 与四类 ref(按 TraceLink.source 桶式映射),把
uncertainties + data_gaps 汇聚为 ``missing_context``。让契约可溯源(spec §4.4/§8 Wave 3)。
"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.reporting.contract import Audience, ReportContract, ReportType
from data_analysis_agent.reporting.model import (
    DataContext,
    ProcessContext,
    SourceKind,
    UserNeed,
)
from data_analysis_agent.reporting.requirement_parser import parse_user_need
from data_analysis_agent.reporting.traceability import link_to_contract_fields

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

_REF_BUCKET: dict[SourceKind, str] = {
    SourceKind.EXPLICIT_USER: "explicit_requirement_refs",
    SourceKind.IMPLICIT_USER: "implicit_requirement_refs",
    SourceKind.DATA_CONTEXT: "data_context_refs",
    SourceKind.PROCESS_CONTEXT: "process_context_refs",
}


class ReportContractTool(Tool):
    """Canonicalize a Report Contract from UserNeed + Data/Process Context (read-only)."""

    @property
    def name(self) -> str:
        return "report_contract"

    @property
    def description(self) -> str:
        return (
            "Canonicalize a Report Contract from a UserNeed + DataContext + ProcessContext "
            "BEFORE heavy analysis. Populates field_sources (per-field origin) and the four "
            "traceability ref buckets so the contract is auditable, and surfaces "
            "missing_context from uncertainties + data gaps. Read-only; use before html_report."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The user's analysis question."},
                "user_need": {
                    "type": "object",
                    "description": "Optional user_need tool output; parsed from question if absent.",
                },
                "data_context": {
                    "type": "object",
                    "description": "Optional data_context portion of report_context output.",
                },
                "process_context": {
                    "type": "object",
                    "description": "Optional process_context portion of report_context output.",
                },
                "report_type": {
                    "type": "string",
                    "description": "Override: daily_kpi/weekly_kpi/diagnostic/recommendation/"
                    "data_quality/funnel/cohort/risk_anomaly/ad_hoc.",
                },
                "audience": {
                    "type": "string",
                    "description": "Override: business_stakeholder/technical.",
                },
                "language": {"type": "string"},
            },
            "required": ["question"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        question = input_data.get("question")
        if not isinstance(question, str) or not question.strip():
            return ValidationResult.fail("question is required and must be a non-empty string")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        question = input_data["question"]
        user_need_dict = input_data.get("user_need")
        if isinstance(user_need_dict, dict):
            try:
                user_need = UserNeed.from_dict(user_need_dict)
            except (TypeError, KeyError):
                # 残缺 dict(缺 explicit/implicit_requirements 等必填键)→ 回退到解析 question
                user_need = parse_user_need(question)
        else:
            user_need = parse_user_need(question)
        dc_dict = input_data.get("data_context")
        data_context = (
            DataContext.from_dict(dc_dict) if isinstance(dc_dict, dict) else DataContext()
        )
        pc_dict = input_data.get("process_context")
        process_context = (
            ProcessContext.from_dict(pc_dict) if isinstance(pc_dict, dict) else ProcessContext()
        )

        report_type = _resolve_report_type(input_data.get("report_type"), user_need)
        audience = _resolve_audience(input_data.get("audience"), user_need)
        language_override = input_data.get("language")
        language = (
            language_override
            if isinstance(language_override, str) and language_override
            else (user_need.explicit_requirements.language or "auto")
        )

        links = link_to_contract_fields(user_need, data_context, process_context)
        field_sources = tuple((lk.target, lk.source) for lk in links)

        refs: dict[str, list[str]] = {
            "explicit_requirement_refs": [],
            "implicit_requirement_refs": [],
            "data_context_refs": [],
            "process_context_refs": [],
        }
        for lk in links:
            bucket = _REF_BUCKET.get(lk.source)
            if bucket and lk.source_ref:
                refs[bucket].append(lk.source_ref)

        missing: list[str] = [u.topic for u in user_need.uncertainties]
        for gap in data_context.data_gaps:
            if gap not in missing:
                missing.append(gap)

        contract = ReportContract(
            question=question,
            report_type=report_type,
            audience=audience,
            language=language,
            data_sources=tuple(tb.path or tb.name for tb in data_context.tables),
            dimensions=tuple(data_context.candidate_dimensions),
            business_grain=data_context.business_grain,
            explicit_requirement_refs=tuple(_dedup(refs["explicit_requirement_refs"])),
            implicit_requirement_refs=tuple(_dedup(refs["implicit_requirement_refs"])),
            data_context_refs=tuple(_dedup(refs["data_context_refs"])),
            process_context_refs=tuple(_dedup(refs["process_context_refs"])),
            field_sources=field_sources,
            missing_context=tuple(missing),
        )
        return ToolResult(content=_render(contract), metadata={"contract": contract.to_dict()})


def _resolve_report_type(override: Any, user_need: UserNeed) -> ReportType:
    raw = (
        override
        if isinstance(override, str) and override
        else user_need.implicit_requirements.likely_report_type
    )
    if not raw:
        return ReportType.AD_HOC
    try:
        return ReportType(raw)
    except ValueError:
        return ReportType.AD_HOC


def _resolve_audience(override: Any, user_need: UserNeed) -> Audience:
    raw = (
        override
        if isinstance(override, str) and override
        else user_need.explicit_requirements.audience
    )
    if not raw:
        return Audience.BUSINESS_STAKEHOLDER
    try:
        return Audience(raw)
    except ValueError:
        return Audience.BUSINESS_STAKEHOLDER


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _render(contract: ReportContract) -> str:
    lines = [
        f"question: {contract.question}",
        f"report_type: {contract.report_type.value}",
        f"audience: {contract.audience.value}",
        f"language: {contract.language}",
    ]
    if contract.data_sources:
        lines.append(f"data_sources: {', '.join(contract.data_sources)}")
    lines.append(
        "traceability: "
        f"explicit={len(contract.explicit_requirement_refs)}, "
        f"implicit={len(contract.implicit_requirement_refs)}, "
        f"data={len(contract.data_context_refs)}, "
        f"process={len(contract.process_context_refs)}, "
        f"field_sources={len(contract.field_sources)}"
    )
    if contract.missing_context:
        lines.append(f"missing_context: {', '.join(contract.missing_context)}")
    return "\n".join(lines)
