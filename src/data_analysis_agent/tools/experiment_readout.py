"""ExperimentReadoutTool:对随机化 A/B 实验数据做读出(只读)。

薄封装 ``causal.experiment.compute_readout``:接受 records(行列表)或 columns(列字典),
按处理臂做效应估计/SRM/护栏/有界决策。统计为正态近似 z 检验(纯 stdlib,确定性)。
强制 outcome_kind=proportion 但结果列非二元 → 校验失败(不静默回退)。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from data_analysis_agent.causal.experiment import compute_readout
from data_analysis_agent.causal.model import OutcomeKind

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

_MAX_TREATMENT_ARMS = 10  # df 表上限(k≤11 → df≤10)


class ExperimentReadoutTool(Tool):
    """Summarize a randomized A/B experiment into a bounded decision (read-only)."""

    @property
    def name(self) -> str:
        return "experiment_readout"

    @property
    def description(self) -> str:
        return (
            "Read out a randomized A/B experiment: per-contrast effect + 95% CI, sample-ratio "
            "mismatch (SRM), guardrails, and a bounded aggregate decision "
            "(ship/do_not_ship/inconclusive/needs_more_data). Pass records (list of row dicts) or "
            "columns (dict of column->values), the group/outcome column, control and treatment arms. "
            "Normal-approximation z-test, deterministic. Read-only."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Optional contract question carried onto the readout label.",
                },
                "records": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Row-wise records; alternative to columns.",
                },
                "columns": {
                    "type": "object",
                    "description": "Columnar data {column_name: [values]}; alternative to records.",
                },
                "control_group": {
                    "type": "string",
                    "description": "Control arm value in group_column.",
                },
                "treatment_groups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Treatment arm values in group_column.",
                },
                "group_column": {"type": "string", "description": "Column holding the arm label."},
                "outcome_column": {
                    "type": "string",
                    "description": "Column holding the outcome metric.",
                },
                "outcome_kind": {
                    "type": "string",
                    "enum": ["auto", "proportion", "mean"],
                    "description": "auto: detect binary->proportion else mean.",
                },
                "guardrail_columns": {"type": "array", "items": {"type": "string"}},
                "guardrail_directions": {
                    "type": "object",
                    "description": "{guardrail_column: higher_is_worse|lower_is_worse}.",
                },
                "segment_columns": {"type": "array", "items": {"type": "string"}},
                "expected_ratio": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Expected allocation ratios across [control, *treatments].",
                },
                "decision_threshold": {
                    "type": "number",
                    "description": "Relative-effect threshold for ship (default 0.0).",
                },
                "min_sample_size": {
                    "type": "integer",
                    "description": "Per-arm minimum (default 30, min 2).",
                },
            },
            "required": ["control_group", "treatment_groups", "group_column", "outcome_column"],
            "oneOf": [{"required": ["records"]}, {"required": ["columns"]}],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        records = input_data.get("records")
        columns = input_data.get("columns")
        if (records is None) == (columns is None):
            return ValidationResult.fail("provide exactly one of records or columns")
        for key in ("control_group", "group_column", "outcome_column"):
            v = input_data.get(key)
            if not isinstance(v, str) or not v.strip():
                return ValidationResult.fail(f"{key} is required and must be a non-empty string")
        tg = input_data.get("treatment_groups")
        if not isinstance(tg, list) or not tg or not all(isinstance(x, str) and x for x in tg):
            return ValidationResult.fail(
                "treatment_groups must be a non-empty list of non-empty strings"
            )
        if len(tg) > _MAX_TREATMENT_ARMS:
            return ValidationResult.fail(
                f"at most {_MAX_TREATMENT_ARMS} treatment arms (SRM df-table limit)"
            )
        ms = input_data.get("min_sample_size", 30)
        if not isinstance(ms, int) or isinstance(ms, bool) or ms < 2:
            return ValidationResult.fail(
                "min_sample_size must be an integer >= 2 (ddof=1 variance needs n>=2)"
            )

        kind = input_data.get("outcome_kind", "auto")
        if kind == "proportion":
            outcome_col = input_data["outcome_column"]
            values = _outcome_values(records, columns, outcome_col)
            numeric_vals = [v for v in values if _is_number(v)]
            if numeric_vals and not all(v in (0, 0.0, 1, 1.0) for v in numeric_vals):
                return ValidationResult.fail(
                    f"outcome_kind=proportion requires outcome_column {outcome_col} values ⊆ {{0,1}}"
                )
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        columns = _resolve_columns(input_data.get("records"), input_data.get("columns"))
        try:
            readout = compute_readout(
                contract_question=input_data.get("question", ""),
                control_arm=input_data["control_group"],
                treatment_arms=tuple(input_data["treatment_groups"]),
                group_column=input_data["group_column"],
                outcome_column=input_data["outcome_column"],
                columns=columns,
                outcome_kind=OutcomeKind(input_data.get("outcome_kind", "auto")),
                guardrail_columns=tuple(input_data.get("guardrail_columns") or ()),
                guardrail_directions=input_data.get("guardrail_directions") or None,
                segment_columns=tuple(input_data.get("segment_columns") or ()),
                expected_ratio=tuple(input_data["expected_ratio"])
                if input_data.get("expected_ratio")
                else None,
                decision_threshold=_as_float(input_data.get("decision_threshold"), 0.0),
                min_sample_size=_as_int(input_data.get("min_sample_size"), 30),
            )
        except ValueError as exc:
            return ToolResult(is_error=True, content=f"experiment_readout: {exc}")
        return ToolResult(
            content=_render(readout), metadata={"experiment_readout": readout.to_dict()}
        )


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v)


def _as_float(v: object, default: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _as_int(v: object, default: int) -> int:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else default


def _resolve_columns(
    records: Sequence[dict[str, Any]] | None,
    columns: dict[str, Sequence[Any]] | None,
) -> dict[str, list[Any]]:
    if isinstance(columns, dict):
        return {str(k): list(v) for k, v in columns.items()}
    out: dict[str, list[Any]] = {}
    for row in records or ():
        if isinstance(row, dict):
            for k, v in row.items():
                out.setdefault(str(k), []).append(v)
    return out


def _outcome_values(
    records: Sequence[dict[str, Any]] | None,
    columns: dict[str, Sequence[Any]] | None,
    outcome_col: str,
) -> list[Any]:
    if isinstance(columns, dict):
        return list(columns.get(outcome_col, ()))
    return [row.get(outcome_col) for row in records or () if isinstance(row, dict)]


def _render(readout: Any) -> str:
    lines = [
        f"outcome: {readout.outcome_column} ({readout.outcome_kind.value})",
        f"control: {readout.control_arm}  total_n: {readout.total_n}",
    ]
    if readout.srm is not None:
        flag = "SRM!" if readout.srm.srm_detected else "ok"
        lines.append(f"SRM: {flag} (chi2={readout.srm.chi_square}, df={readout.srm.df})")
    for c in readout.contrasts:
        est = c.outcome_estimate
        ci = f"[{est.ci_lower:.4g}, {est.ci_upper:.4g}]" if est.ci_lower is not None else "n/a"
        lines.append(
            f"contrast {c.treatment_arm}: effect={est.effect} ci={ci} decision={c.decision.value}"
        )
    lines.append(f"aggregate_decision: {readout.aggregate_decision.value}")
    if readout.aggregate_reasons:
        lines.append(f"reasons: {', '.join(readout.aggregate_reasons)}")
    return "\n".join(lines)
