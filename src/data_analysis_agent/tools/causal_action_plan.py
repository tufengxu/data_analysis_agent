"""CausalActionPlanTool:把实验读出转成有界行动计划(只读)。

薄封装 ``causal.experiment.build_action_plan``:从 ``ExperimentReadout`` 的聚合决策 +
原因产出 ``ActionPlan``(机制/假设/监控/回滚/反驳)。每条建议都挂在证据上;SRM/护栏
即使在 inconclusive 下也作为可见风险列出,但不升级为 ship。
"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.causal.experiment import build_action_plan
from data_analysis_agent.causal.model import CausalContract, ExperimentReadout

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class CausalActionPlanTool(Tool):
    """Produce a bounded action plan from an experiment readout (read-only)."""

    @property
    def name(self) -> str:
        return "causal_action_plan"

    @property
    def description(self) -> str:
        return (
            "Turn an experiment_readout into a bounded action plan tied to evidence: decision + "
            "recommendations (ship/hold/fix_srm/add_power/drop_arm/investigate_guardrail), "
            "assumptions, refutations to run, and open risks with rollback/monitoring. Never "
            "upgrades SRM-contaminated or inconclusive evidence to ship. Read-only."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "experiment_readout": {
                    "type": "object",
                    "description": "The experiment_readout tool output.",
                },
                "causal_contract": {
                    "type": "object",
                    "description": "Optional causal_contract output (for assumptions).",
                },
            },
            "required": ["experiment_readout"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        readout = input_data.get("experiment_readout")
        if not isinstance(readout, dict):
            return ValidationResult.fail(
                "experiment_readout is required and must be the readout object"
            )
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        readout = ExperimentReadout.from_dict(input_data["experiment_readout"])
        contract_dict = input_data.get("causal_contract")
        contract = (
            CausalContract.from_dict(contract_dict) if isinstance(contract_dict, dict) else None
        )
        plan = build_action_plan(readout, contract)
        return ToolResult(content=_render(plan), metadata={"causal_action_plan": plan.to_dict()})


def _render(plan: Any) -> str:
    lines = [f"decision: {plan.decision.value}"]
    for rec in plan.recommendations:
        line = f"- {rec.code}" + (f" ({rec.target_arm})" if rec.target_arm else "")
        if rec.rationale:
            line += f": {rec.rationale}"
        lines.append(line)
    if plan.open_risks:
        lines.append("risks: " + "; ".join(plan.open_risks))
    return "\n".join(lines)
