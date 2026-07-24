"""MetricContractTool: canonicalize a metric口径 + validate + memory cross-check (read-only).

The metric analog of ``ReportContractTool``. Today a metric's口径 lives in the
model's head and the ad-hoc pandas it writes each turn; there is no check that
the口径 is complete (numerator/denominator/aggregation all empty) or consistent
with the definition the user persisted via ``/define`` (``metric_definition``
memory). This tool pins the口径 down before the model computes anything:

- normalize the structured fields into a ``MetricSpec`` (the reporting domain
  object, now carrying ``exclusions``) so it can flow into ReportContract /
  html_report unchanged;
- validate completeness (reuse the report QA judgment: a metric with neither
  numerator, denominator, nor aggregation cannot be computed) and flag the
  common口径 gaps (time window without grain; grain without timezone; denominator
  without numerator);
- cross-check the supplied memory definition (the model has metric_definitions
  injected into its context — it passes the relevant one in): confirmed vs
  unconfirmed vs absent, and name consistency.

Read-only, stateless, no path whitelist (pure data canonicalization, like
``report_contract`` — it never reads files or writes memory; the memory
definition arrives as an optional input dict).
"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.reporting.contract import MetricSpec
from data_analysis_agent.reporting.model import SourceKind

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


def _as_str(value: Any) -> str | None:
    """Coerce to a stripped non-empty string, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a list/tuple of strings into a tuple of non-empty stripped strings."""
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
    return tuple(out)


def _build_spec(input_data: dict[str, Any]) -> MetricSpec:
    name = (input_data.get("name") or "").strip()
    return MetricSpec(
        name=name,
        source_columns=_as_str_tuple(input_data.get("source_columns")),
        numerator=_as_str(input_data.get("numerator")),
        denominator=_as_str(input_data.get("denominator")),
        aggregation=_as_str(input_data.get("aggregation")),
        filters=_as_str_tuple(input_data.get("filters")),
        exclusions=_as_str_tuple(input_data.get("exclusions")),
        time_window=_as_str(input_data.get("time_window")),
        grain=_as_str(input_data.get("grain")),
        timezone=_as_str(input_data.get("timezone")),
        unit=_as_str(input_data.get("unit")),
        confirmed=bool(input_data.get("confirmed", False)),
        source=SourceKind.EXPLICIT_USER,  # the model is stating the口径 explicitly
    )


def _signature(spec: MetricSpec) -> str:
    """Core口径 signature: name|numerator|denominator|aggregation|grain.

    One-directional: two specs with DIFFERENT signatures are certainly different
    口径 (drift). The reverse is NOT guaranteed — specs sharing a signature may
    still differ in filters/exclusions/time_window; compare the full ``metric``
    dict for full fidelity. Normalized to lowercase + single-spaced so cosmetic
    edits don't flip it.
    """

    def norm(s: str | None) -> str:
        return " ".join((s or "").lower().split())

    return "|".join(
        norm(x) for x in (spec.name, spec.numerator, spec.denominator, spec.aggregation, spec.grain)
    )


def _completeness_findings(spec: MetricSpec) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    has_num = spec.numerator or spec.denominator or spec.aggregation
    if not has_num:
        findings.append(
            {
                "severity": "error",
                "code": "incomplete",
                "message": ("口径不完整：numerator/denominator/aggregation 全空，无法计算该指标"),
            }
        )
    if spec.time_window and not spec.grain:
        findings.append(
            {
                "severity": "warning",
                "code": "missing_grain",
                "message": "有时间窗 (time_window) 但未声明粒度 (grain)",
            }
        )
    if spec.grain and not spec.timezone:
        findings.append(
            {
                "severity": "warning",
                "code": "missing_timezone",
                "message": "有时间粒度 (grain) 但未声明时区 (timezone，默认按 UTC 处理)",
            }
        )
    if spec.denominator and not spec.numerator:
        findings.append(
            {
                "severity": "warning",
                "code": "denominator_without_numerator",
                "message": "有分母 (denominator) 但无分子 (numerator)",
            }
        )
    return findings


def _memory_link(
    spec: MetricSpec, memory_definition: Any
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Cross-check the spec against an optional memory definition dict.

    Returns (memory_link, findings). The memory definition is free text, so we
    surface it as the authority and check name consistency only — we deliberately
    do NOT pretend to fuzzy-match numerator/denominator out of prose.
    """
    findings: list[dict[str, str]] = []
    if not isinstance(memory_definition, dict):
        findings.append(
            {
                "severity": "info",
                "code": "no_memory_definition",
                "message": (
                    f"memory 中无「{spec.name}」的口径定义；"
                    f"用 /define {spec.name}=<口径> 固化以便复用与对齐"
                ),
            }
        )
        return {"present": False, "key": None, "confirmed": False, "content": None}, findings

    key = _as_str(memory_definition.get("key")) or spec.name
    confirmed = bool(memory_definition.get("confirmed", False))
    content = _as_str(memory_definition.get("content"))
    link: dict[str, Any] = {
        "present": True,
        "key": key,
        "confirmed": confirmed,
        "content": content,
    }
    if spec.name and key and spec.name.lower() != key.lower():
        findings.append(
            {
                "severity": "warning",
                "code": "name_mismatch",
                "message": f"指标名「{spec.name}」与 memory 记录的 key「{key}」不一致",
            }
        )
    if confirmed:
        findings.append(
            {
                "severity": "info",
                "code": "confirmed_in_memory",
                "message": "memory 中已有已确认口径，作为权威来源；确保计算口径与其一致",
            }
        )
    else:
        findings.append(
            {
                "severity": "warning",
                "code": "unconfirmed_in_memory",
                "message": "memory 中的口径定义尚未确认（light-confirm 待定），计算前请与用户核对",
            }
        )
    return link, findings


def _severity_rank(sev: str) -> int:
    return {"error": 0, "warning": 1, "info": 2}.get(sev, 3)


def _render(
    spec: MetricSpec, signature: str, memory_link: dict[str, Any], findings: list[dict[str, str]]
) -> str:
    lines = [f"metric: {spec.name}"]
    bits = []
    if spec.numerator:
        bits.append(f"num={spec.numerator}")
    if spec.denominator:
        bits.append(f"den={spec.denominator}")
    if spec.aggregation:
        bits.append(f"agg={spec.aggregation}")
    if spec.unit:
        bits.append(f"unit={spec.unit}")
    lines.append("  口径: " + (" · ".join(bits) if bits else "<incomplete>"))
    if spec.filters:
        lines.append(f"  filters: {', '.join(spec.filters)}")
    if spec.exclusions:
        lines.append(f"  exclusions: {', '.join(spec.exclusions)}")
    tw = []
    if spec.time_window:
        tw.append(f"window={spec.time_window}")
    if spec.grain:
        tw.append(f"grain={spec.grain}")
    if spec.timezone:
        tw.append(f"tz={spec.timezone}")
    if tw:
        lines.append("  time: " + " · ".join(tw))
    lines.append(f"  signature: {signature}")
    if memory_link["present"]:
        conf = "confirmed" if memory_link["confirmed"] else "UNCONFIRMED"
        lines.append(f"  memory: {memory_link['key']} ({conf})")
    else:
        lines.append("  memory: <no stored definition>")
    if findings:
        lines.append("  findings:")
        for f in findings:
            lines.append(f"    - [{f['severity']}] {f['code']}: {f['message']}")
    return "\n".join(lines)


class MetricContractTool(Tool):
    """Canonicalize a metric口径, validate it, and cross-check memory (read-only).

    Pin down a metric's definition (numerator/denominator/aggregation, filters,
    exclusions, time window, grain, timezone, unit) into an auditable MetricSpec
    BEFORE computing it. Validates completeness (a metric needs at least one of
    numerator/denominator/aggregation) and口径 gaps. Optionally pass a
    ``memory_definition`` ({key, content, confirmed}) — as injected into your
    context — to confirm owner status and flag name drift. Read-only; no files.
    """

    @property
    def name(self) -> str:
        return "metric_contract"

    @property
    def description(self) -> str:
        return (
            "Canonicalize a metric's口径 (numerator/denominator/aggregation, filters, "
            "exclusions, time window, grain, timezone, unit) into an auditable "
            "MetricSpec BEFORE computing it. Validates completeness (needs ≥1 of "
            "numerator/denominator/aggregation) and口径 gaps (time window without "
            "grain, etc.). Optionally pass a memory_definition ({key, content, "
            "confirmed}) to confirm owner status and flag name drift. Read-only."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Metric name (required)."},
                "source_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Source columns the metric is computed from (for traceability).",
                },
                "numerator": {"type": "string", "description": "Numerator definition / source."},
                "denominator": {
                    "type": "string",
                    "description": "Denominator definition / source.",
                },
                "aggregation": {
                    "type": "string",
                    "description": "Aggregation if not a ratio (sum/avg/count/...).",
                },
                "filters": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter clauses applied before computing.",
                },
                "exclusions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Excluded segments (e.g. refunds, internal accounts).",
                },
                "time_window": {"type": "string", "description": "e.g. last_7d, 2024-01."},
                "grain": {"type": "string", "description": "e.g. day/week/month."},
                "timezone": {"type": "string", "description": "e.g. UTC, Asia/Shanghai."},
                "unit": {"type": "string", "description": "e.g. USD, %, count."},
                "confirmed": {
                    "type": "boolean",
                    "description": "Mark the口径 as owner-confirmed in this call.",
                },
                "memory_definition": {
                    "type": "object",
                    "description": (
                        "Optional stored definition from memory (as injected into "
                        "your context): {key, content, confirmed}. Used to confirm "
                        "owner status and flag name drift."
                    ),
                },
            },
            "required": ["name"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        name = input_data.get("name")
        if not isinstance(name, str) or not name.strip():
            return ValidationResult.fail("name is required and must be a non-empty string")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        # Defense in depth: validate_input runs in the harness before call(), but
        # call() is also invoked directly (tests, eval) — name is the one field
        # everything keys off, so guard it here too.
        name = (
            (input_data.get("name") or "").strip()
            if isinstance(input_data.get("name"), str)
            else ""
        )
        if not name:
            return ToolResult(
                content="Error: name is required and must be a non-empty string",
                is_error=True,
            )
        spec = _build_spec(input_data)
        # owner-confirmed is true when explicitly stated OR a confirmed memory
        # definition backs it (resolved after the memory cross-check below).
        signature = _signature(spec)
        memory_link, mem_findings = _memory_link(spec, input_data.get("memory_definition"))
        owner_confirmed = bool(spec.confirmed) or (
            memory_link["present"] and bool(memory_link["confirmed"])
        )
        findings = _completeness_findings(spec) + mem_findings
        findings.sort(key=lambda f: _severity_rank(f["severity"]))

        contract = {
            "metric": spec.to_dict(),
            "owner_confirmed": owner_confirmed,
            "signature": signature,
            "memory_link": memory_link,
            "findings": findings,
        }
        return ToolResult(
            content=_render(spec, signature, memory_link, findings),
            metadata={"metric_contract": contract},
        )
