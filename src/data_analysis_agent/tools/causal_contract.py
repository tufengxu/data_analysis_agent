"""CausalContractTool:把用户问题 + 上下文 归一化为 CausalContract(只读)。

薄封装 ``causal.intent``/``causal.model``:从问题抽因果意图,推断 claim_level 与分配机制,
把用户/agent 提供的处理/结果/护栏/假设显式化,缺项写入 ``missing_context``(不臆测)。
假设/混淆默认标 ``IMPLICIT_USER``,除非显式确认(anti-hallucination)。
"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.causal.intent import infer_claim_level, parse_causal_intent
from data_analysis_agent.causal.model import (
    AssignmentMechanism,
    CausalContract,
)

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

_ASSIGN_MAP: dict[str, AssignmentMechanism] = {
    "randomized": AssignmentMechanism.RANDOMIZED,
    "quasi_experiment": AssignmentMechanism.QUASI_EXPERIMENT,
    "self_selection": AssignmentMechanism.SELF_SELECTION,
    "unknown": AssignmentMechanism.UNKNOWN,
}


class CausalContractTool(Tool):
    """Build a Causal Contract from a question + optional context (read-only)."""

    @property
    def name(self) -> str:
        return "causal_contract"

    @property
    def description(self) -> str:
        return (
            "Build a Causal Contract from a question BEFORE experiment_readout. Detects causal "
            "intent, infers claim_level (descriptive/associational/causal_assumption/experimental) "
            "and assignment mechanism, and surfaces missing_context (treatment/outcome/control) "
            "without guessing. Assumptions are inferred (implicit) unless user-confirmed. Read-only."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The user's causal/decision question.",
                },
                "business_assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Identifiability/ignorability/SUTVA assumptions (explicit only if user-confirmed).",
                },
                "external_events": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Concurrent confounders in the analysis window.",
                },
                "treatment_column": {"type": "string"},
                "control_arm": {"type": "string"},
                "treatment_arms": {"type": "array", "items": {"type": "string"}},
                "outcome_columns": {"type": "array", "items": {"type": "string"}},
                "guardrail_columns": {"type": "array", "items": {"type": "string"}},
                "segment_columns": {"type": "array", "items": {"type": "string"}},
                "assignment_mechanism": {
                    "type": "string",
                    "enum": ["randomized", "quasi_experiment", "self_selection", "unknown"],
                },
                "decision_threshold": {"type": "number"},
                "min_sample_size": {"type": "integer"},
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
        intent = parse_causal_intent(question)

        assumptions = tuple(input_data.get("business_assumptions") or ())
        external_events = tuple(input_data.get("external_events") or ())
        claim_level = infer_claim_level(intent, bool(assumptions))

        assign_override = input_data.get("assignment_mechanism")
        if isinstance(assign_override, str) and assign_override in _ASSIGN_MAP:
            assignment = _ASSIGN_MAP[assign_override]
        elif intent.has_randomization_signal:
            assignment = AssignmentMechanism.RANDOMIZED
        else:
            assignment = AssignmentMechanism.UNKNOWN

        treatment_column = input_data.get("treatment_column")
        treatment_column = (
            treatment_column if isinstance(treatment_column, str) and treatment_column else None
        )
        control_arm = input_data.get("control_arm")
        control_arm = control_arm if isinstance(control_arm, str) and control_arm else None
        treatment_arms = tuple(input_data.get("treatment_arms") or ())
        outcome_columns = tuple(input_data.get("outcome_columns") or ())
        guardrail_columns = tuple(input_data.get("guardrail_columns") or ())
        segment_columns = tuple(input_data.get("segment_columns") or ())
        decision_threshold = _as_float(input_data.get("decision_threshold"), 0.0)
        min_sample_size = _as_int(input_data.get("min_sample_size"), 30)

        missing = _missing_context(
            intent, treatment_column, treatment_arms, outcome_columns, control_arm, assignment
        )

        contract = CausalContract(
            question=question,
            claim_level=claim_level,
            assignment_mechanism=assignment,
            outcome_columns=outcome_columns,
            treatment_column=treatment_column,
            control_arm=control_arm,
            treatment_arms=treatment_arms,
            guardrail_columns=guardrail_columns,
            segment_columns=segment_columns,
            decision_threshold=decision_threshold,
            min_sample_size=min_sample_size,
            business_assumptions=assumptions,
            external_events=external_events,
            missing_context=tuple(missing),
            intent=intent,
        )
        return ToolResult(
            content=_render(contract), metadata={"causal_contract": contract.to_dict()}
        )


def _as_float(v: object, default: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _as_int(v: object, default: int) -> int:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else default


def _missing_context(
    intent: Any,
    treatment_column: str | None,
    treatment_arms: tuple[str, ...],
    outcome_columns: tuple[str, ...],
    control_arm: str | None,
    assignment: AssignmentMechanism,
) -> list[str]:
    causal = intent.has_intervention or intent.has_randomization_signal
    missing: list[str] = []
    if causal and not treatment_column and not treatment_arms:
        missing.append("treatment(处理列/处理臂)")
    if causal and not outcome_columns:
        missing.append("outcome(结果列)")
    if assignment is AssignmentMechanism.RANDOMIZED and control_arm is None:
        missing.append("control_arm(对照臂)")
    return missing


def _render(contract: CausalContract) -> str:
    lines = [
        f"question: {contract.question}",
        f"claim_level: {contract.claim_level.value}",
        f"assignment_mechanism: {contract.assignment_mechanism.value}",
        f"intent: {contract.intent.rationale}",
    ]
    if contract.treatment_column:
        lines.append(f"treatment_column: {contract.treatment_column}")
    if contract.treatment_arms:
        lines.append(f"treatment_arms: {', '.join(contract.treatment_arms)}")
    if contract.control_arm:
        lines.append(f"control_arm: {contract.control_arm}")
    if contract.outcome_columns:
        lines.append(f"outcome_columns: {', '.join(contract.outcome_columns)}")
    if contract.guardrail_columns:
        lines.append(f"guardrail_columns: {', '.join(contract.guardrail_columns)}")
    if contract.business_assumptions:
        lines.append(
            f"business_assumptions: {len(contract.business_assumptions)} (inferred unless confirmed)"
        )
    if contract.missing_context:
        lines.append(f"missing_context: {', '.join(contract.missing_context)}")
    return "\n".join(lines)
