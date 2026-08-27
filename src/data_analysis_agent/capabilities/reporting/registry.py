"""reporting 能力域注册表(v2 委托式迁移)。

五个能力:

- ``reporting_report_need`` / ``reporting_report_context`` / ``reporting_report_contract``
  —— 委托纯 stdlib 领域层 ``reporting.*``,行为与 v1 同名工具一致(requirement_parser /
  context_collector / traceability + templates + overlays)。
- ``reporting_render_chart`` —— 委托 ``tools.chart_render.ChartRenderTool``(结构化
  ChartSpec + 数据 → ECharts option + JSON 产物)。
- ``reporting_render_html`` —— 委托 ``tools.html_report.HtmlReportTool``(自包含
  ECharts H5 页面;输出强制限定产物目录,文本全转义,DRAFT 文档被 QA 闸拒绝)。

工具委托统一经 ``await tool.call(input)`` 并把 ``ToolResult`` 映射为
``CapabilityOutput``;``is_error`` → ``CapabilityError("execution_error", ...)``,保持
fail-closed 信封。渲染类能力权限为 WRITES_ARTIFACTS,产物真实路径进 ``artifacts``。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from data_analysis_agent.reporting.context_collector import (
    build_data_context,
    build_process_context,
)
from data_analysis_agent.reporting.contract import Audience, ReportContract, ReportType
from data_analysis_agent.reporting.model import (
    DataContext,
    ProcessContext,
    SourceKind,
    UserNeed,
)
from data_analysis_agent.reporting.overlays import apply_overlay
from data_analysis_agent.reporting.requirement_parser import parse_user_need
from data_analysis_agent.reporting.templates import select_template
from data_analysis_agent.reporting.traceability import link_to_contract_fields
from data_analysis_agent.tools.chart_render import ChartRenderTool
from data_analysis_agent.tools.html_report import HtmlReportTool

from ..contracts import (
    CapabilityError,
    CapabilityHandler,
    CapabilityOutput,
    CapabilityRegistry,
    CapabilitySpec,
    OutputKind,
    Permission,
)

_REF_BUCKET: dict[SourceKind, str] = {
    SourceKind.EXPLICIT_USER: "explicit_requirement_refs",
    SourceKind.IMPLICIT_USER: "implicit_requirement_refs",
    SourceKind.DATA_CONTEXT: "data_context_refs",
    SourceKind.PROCESS_CONTEXT: "process_context_refs",
}

_ERROR_CODES = ("validation_error", "execution_error")

# ----------------------------- 输入 schema(镜像 v1 工具) -----------------------------

_NEED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "raw_request": {
            "type": "string",
            "description": "The user's raw report request (natural language).",
        },
    },
    "required": ["raw_request"],
}

_CONTEXT_SCHEMA: dict[str, Any] = {
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

_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "The user's analysis question."},
        "user_need": {
            "type": "object",
            "description": "Optional reporting_report_need output's user_need; parsed if absent.",
        },
        "data_context": {
            "type": "object",
            "description": ("Optional data_context portion of reporting_report_context output."),
        },
        "process_context": {
            "type": "object",
            "description": ("Optional process_context portion of reporting_report_context output."),
        },
        "report_type": {
            "type": "string",
            "description": (
                "Override: daily_kpi/weekly_kpi/diagnostic/recommendation/data_quality/"
                "funnel/cohort/risk_anomaly/ad_hoc."
            ),
        },
        "audience": {"type": "string", "description": "Override: business_stakeholder/technical."},
        "language": {"type": "string"},
        "domain": {
            "type": "string",
            "description": (
                "Optional business domain for a domain-specific caveat overlay: "
                "retail/saas/finance/operations/risk/marketing. Adds domain-specific "
                "required_caveats to the template."
            ),
        },
    },
    "required": ["question"],
}

# ----------------------------- 渲染(镜像 v1 工具的 _render) -----------------------------


def _render_need(need: UserNeed) -> str:
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


def _render_context(data_context: DataContext, process_context: ProcessContext) -> str:
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


def _render_contract(contract: ReportContract, template: Any = None) -> str:
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
    if template is not None:
        roles = ", ".join(r.value for r in template.section_roles)
        caveats = ", ".join(template.required_caveats) if template.required_caveats else "(none)"
        lines.append(f"template[{template.name}] section_roles: {roles}")
        lines.append(f"template required_caveats: {caveats}")
    return "\n".join(lines)


# ----------------------------- 纯领域层 handlers -----------------------------


async def _report_need(inputs: dict[str, Any]) -> CapabilityOutput:
    """委托 ``reporting.requirement_parser.parse_user_need``(镜像 ReportNeedTool)。"""

    raw = inputs.get("raw_request")
    if not isinstance(raw, str) or not raw.strip():
        raise CapabilityError(
            "validation_error", "raw_request is required and must be a non-empty string"
        )
    need = parse_user_need(raw)
    return CapabilityOutput(
        content=_render_need(need),
        data={"user_need": need.to_dict()},
    )


async def _report_context(inputs: dict[str, Any]) -> CapabilityOutput:
    """委托 ``reporting.context_collector``(镜像 ReportContextTool)。"""

    profile = inputs.get("profile")
    if not isinstance(profile, dict):
        raise CapabilityError(
            "validation_error", "profile is required and must be an object (data_profile output)"
        )
    events_raw = inputs.get("events")
    events = events_raw if isinstance(events_raw, list) else []
    sensitive = inputs.get("sensitive_mode") is True
    data_context = build_data_context(profile)
    process_context = build_process_context(events, sensitive_mode=sensitive)
    return CapabilityOutput(
        content=_render_context(data_context, process_context),
        data={
            "data_context": data_context.to_dict(),
            "process_context": process_context.to_dict(),
        },
    )


async def _report_contract(inputs: dict[str, Any]) -> CapabilityOutput:
    """委托 Wave 1-2 reporting 纯层(镜像 ReportContractTool,含溯源与模板接线)。"""

    question = inputs.get("question")
    if not isinstance(question, str) or not question.strip():
        raise CapabilityError(
            "validation_error", "question is required and must be a non-empty string"
        )
    user_need_dict = inputs.get("user_need")
    if isinstance(user_need_dict, dict):
        try:
            user_need = UserNeed.from_dict(user_need_dict)
        except (TypeError, KeyError):
            # 残缺 dict(缺 explicit/implicit_requirements 等必填键)→ 回退到解析 question
            user_need = parse_user_need(question)
    else:
        user_need = parse_user_need(question)
    dc_dict = inputs.get("data_context")
    data_context = DataContext.from_dict(dc_dict) if isinstance(dc_dict, dict) else DataContext()
    pc_dict = inputs.get("process_context")
    process_context = (
        ProcessContext.from_dict(pc_dict) if isinstance(pc_dict, dict) else ProcessContext()
    )

    report_type = _resolve_report_type(inputs.get("report_type"), user_need)
    audience = _resolve_audience(inputs.get("audience"), user_need)
    language_override = inputs.get("language")
    language = (
        language_override
        if isinstance(language_override, str) and language_override
        else (user_need.explicit_requirements.language or "auto")
    )
    domain = _as_optional_str(inputs.get("domain"))
    if domain:
        domain = domain.lower()  # normalize so SAAS/SaaS hit the overlay table too

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
        domain=domain,
        explicit_requirement_refs=tuple(_dedup(refs["explicit_requirement_refs"])),
        implicit_requirement_refs=tuple(_dedup(refs["implicit_requirement_refs"])),
        data_context_refs=tuple(_dedup(refs["data_context_refs"])),
        process_context_refs=tuple(_dedup(refs["process_context_refs"])),
        field_sources=field_sources,
        missing_context=tuple(missing),
    )
    # 接线确定性模板选择器 + 领域 caveat overlay(AD_HOC/未知 report_type → 无模板)。
    template = select_template(contract.report_type)
    if template is not None and domain:
        template = apply_overlay(template, domain)
    data: dict[str, Any] = {"contract": contract.to_dict()}
    if template is not None:
        data["template"] = template.to_dict()
    return CapabilityOutput(
        content=_render_contract(contract, template),
        data=data,
    )


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


def _as_optional_str(value: Any) -> str | None:
    """Coerce to a stripped non-empty string, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


# ----------------------------- 工具委托(渲染产物) -----------------------------


async def _run_tool(tool: Any, inputs: dict[str, Any]) -> CapabilityOutput:
    """validate → call → 映射 ToolResult;is_error → execution_error(fail-closed)。"""

    validation = tool.validate_input(inputs)
    if not validation.valid:
        raise CapabilityError("validation_error", validation.error or "invalid input")
    result = await tool.call(inputs)
    if result.is_error:
        raise CapabilityError("execution_error", result.content or "tool execution failed")
    artifacts = tuple(str(p) for p in result.metadata.get("artifact_paths", ()))
    return CapabilityOutput(
        content=result.content,
        metadata=dict(result.metadata),
        artifacts=artifacts,
    )


def _with_artifact_dir(base: Any, inputs: dict[str, Any]) -> Any:
    """Per-call ``artifact_dir`` override:同类型新实例;缺省用 registry 级实例。"""

    override = inputs.get("artifact_dir")
    if isinstance(override, str) and override.strip():
        return type(base)(artifact_dir=override)
    return base


# ----------------------------- 注册 -----------------------------


def register_all(registry: CapabilityRegistry, *, artifact_root: Path | None = None) -> list[str]:
    """把 reporting 域的五个能力注册进 ``registry``,返回注册名列表(注册顺序)。

    ``artifact_root`` 指定渲染类能力(chart/html)的产物根目录(测试传 ``tmp_path``);
    缺省时为每次 ``register_all`` 调用创建独立临时目录。图表与报告分别落在
    ``charts/`` 与 ``reports/`` 子目录下,均限定在产物根之内。
    """

    root = (
        Path(artifact_root).expanduser()
        if artifact_root is not None
        else Path(tempfile.mkdtemp(prefix="daa_reporting_cap_"))
    )
    chart_tool = ChartRenderTool(artifact_dir=root / "charts")
    html_tool = HtmlReportTool(artifact_dir=root / "reports")

    # 渲染类 schema 直接取自工具(保持单一事实源),仅追加 artifact_dir 覆盖参数。
    chart_schema: dict[str, Any] = {
        **chart_tool.input_schema,
        "properties": {
            **chart_tool.input_schema["properties"],
            "artifact_dir": {
                "type": "string",
                "description": "Optional artifact directory override (default: registry root).",
            },
        },
    }
    html_schema: dict[str, Any] = {
        **html_tool.input_schema,
        "properties": {
            **html_tool.input_schema["properties"],
            "artifact_dir": {
                "type": "string",
                "description": "Optional artifact directory override (default: registry root).",
            },
        },
    }

    async def render_chart(inputs: dict[str, Any]) -> CapabilityOutput:
        tool = _with_artifact_dir(chart_tool, inputs)
        output = await _run_tool(tool, inputs)
        return CapabilityOutput(
            content=output.content,
            data={
                "chart_option": output.metadata.get("chart_option", {}),
                "chart_meta": output.metadata.get("chart_meta", {}),
            },
            metadata=output.metadata,
            artifacts=output.artifacts,
        )

    async def render_html(inputs: dict[str, Any]) -> CapabilityOutput:
        tool = _with_artifact_dir(html_tool, inputs)
        return await _run_tool(tool, inputs)

    entries: list[tuple[CapabilitySpec, CapabilityHandler]] = [
        (
            CapabilitySpec(
                name="reporting_report_need",
                description=(
                    "Parse a raw report request into a UserNeed: separates EXPLICIT requirements "
                    "(lexical facts: requested outputs, audience, language) from IMPLICIT "
                    "inferences (likely report type, cadence, narrative style), and lists "
                    "uncertainties + whether a clarification is needed. Read-only; use before "
                    "reporting_report_contract."
                ),
                input_schema=_NEED_SCHEMA,
                domain="reporting",
                output_kind=OutputKind.STRUCTURED,
                permission=Permission.READ_ONLY,
                error_codes=_ERROR_CODES,
            ),
            _report_need,
        ),
        (
            CapabilitySpec(
                name="reporting_report_context",
                description=(
                    "Collect Data Context (from a data_profile result object) and Process Context "
                    "(from summarized tool-event objects) into structured reporting context: "
                    "candidate date/metric/dimension columns, business grain, tool steps, "
                    "assumptions. Pass sensitive_mode=true to drop process detail for privacy. "
                    "Read-only; use before and after analysis."
                ),
                input_schema=_CONTEXT_SCHEMA,
                domain="reporting",
                output_kind=OutputKind.STRUCTURED,
                permission=Permission.READ_ONLY,
                error_codes=_ERROR_CODES,
            ),
            _report_context,
        ),
        (
            CapabilitySpec(
                name="reporting_report_contract",
                description=(
                    "Canonicalize a Report Contract from a UserNeed + DataContext + "
                    "ProcessContext BEFORE heavy analysis. Populates field_sources (per-field "
                    "origin) and the four traceability ref buckets so the contract is auditable, "
                    "and surfaces missing_context from uncertainties + data gaps. Read-only; use "
                    "before reporting_render_html."
                ),
                input_schema=_CONTRACT_SCHEMA,
                domain="reporting",
                output_kind=OutputKind.STRUCTURED,
                permission=Permission.READ_ONLY,
                error_codes=_ERROR_CODES,
            ),
            _report_contract,
        ),
        (
            CapabilitySpec(
                name="reporting_render_chart",
                description=(
                    "Render a structured chart request into an ECharts option + JSON artifact, "
                    "WITHOUT writing free-form Python. Pass family (line/bar/grouped_bar/"
                    "stacked_bar/scatter/heatmap/funnel/waterfall/dot) + data; returns the "
                    "chart_option (feed it into reporting_render_html's charts map under the "
                    "same block_id) + chart metadata (family, data_sufficient, n_points, "
                    "fallback_family) + the artifact path. Writes artifacts."
                ),
                input_schema=chart_schema,
                domain="reporting",
                output_kind=OutputKind.ARTIFACT,
                permission=Permission.WRITES_ARTIFACTS,
                error_codes=_ERROR_CODES,
            ),
            render_chart,
        ),
        (
            CapabilitySpec(
                name="reporting_render_html",
                description=(
                    "Generate a self-contained H5 HTML analysis report with ECharts charts. "
                    "PREFERRED: pass a `document` (ReportDocument) built from "
                    "reporting_report_contract — it is run through the deterministic QA gate and "
                    "a DRAFT (blocker) document is REFUSED (no file written). LEGACY: the old "
                    "title/summary/sections form still renders but WITHOUT the QA gate. Output "
                    "is confined to the artifact directory; the report file path is returned as "
                    "the delivered artifact. Writes artifacts."
                ),
                input_schema=html_schema,
                domain="reporting",
                output_kind=OutputKind.ARTIFACT,
                permission=Permission.WRITES_ARTIFACTS,
                error_codes=_ERROR_CODES,
            ),
            render_html,
        ),
    ]
    for spec, handler in entries:
        registry.register(spec, handler)
    return [spec.name for spec, _ in entries]
