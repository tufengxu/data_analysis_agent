"""CausalReportTool: causal 读出 → ReportDocument 适配(只读)。

薄封装 ``causal.report_adapter.to_report_document``:把 experiment_readout +
causal_contract + causal_qa(+ 可选 causal_action_plan)转成 ``ReportDocument``,
其块序列满足 reporting QA 的因果规则(每个 FINDING 紧跟一个 CAVEAT,中性措辞,
因果语留给 caveat)。让 causal 结果经同一 QA 闸门进入 html_report v2 交付路径
(闭合"causal/report_adapter 是死代码"的 G3 缺口)。
"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.causal.model import (
    ActionPlan,
    CausalContract,
    CausalQAReport,
    ExperimentReadout,
    OutcomeKind,
)
from data_analysis_agent.causal.report_adapter import to_report_document

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class CausalReportTool(Tool):
    """Build a ReportDocument from causal results so they render through the QA gate."""

    @property
    def name(self) -> str:
        return "causal_report"

    @property
    def description(self) -> str:
        return (
            "Turn causal_contract + causal_qa + experiment_readout (+ optional "
            "causal_action_plan) into a ReportDocument for html_report(document=...). "
            "Places a CAVEAT immediately after every causal FINDING and uses neutral "
            "phrasing (difference of / lift of / associated with); causal language stays "
            "in caveats. Read-only — pass its `document` output to html_report."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "causal_contract": {
                    "type": "object",
                    "description": "causal_contract tool output.",
                },
                "causal_qa": {"type": "object", "description": "causal_qa tool output."},
                "experiment_readout": {
                    "type": "object",
                    "description": "experiment_readout tool output (required for an experiment; "
                    "pass an empty stub {contrasts:[]} for an observational-only readout).",
                },
                "causal_action_plan": {
                    "type": "object",
                    "description": "Optional causal_action_plan tool output.",
                },
            },
            "required": ["causal_contract", "causal_qa"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        if not isinstance(input_data.get("causal_contract"), dict):
            return ValidationResult.fail(
                "causal_contract is required and must be the contract object"
            )
        if not isinstance(input_data.get("causal_qa"), dict):
            return ValidationResult.fail("causal_qa is required and must be the qa object")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        contract = CausalContract.from_dict(input_data["causal_contract"])
        qa = CausalQAReport.from_dict(input_data["causal_qa"])
        readout_dict = input_data.get("experiment_readout")
        # Observational-only questions have no experiment_readout; synthesize an empty
        # readout so the adapter can still produce a FINDING/CAVEAT-bearing document.
        readout = (
            ExperimentReadout.from_dict(readout_dict)
            if isinstance(readout_dict, dict)
            else ExperimentReadout(
                contract_question=contract.question,
                control_arm="",
                outcome_column="",
                outcome_kind=OutcomeKind.AUTO,
                aggregate_reasons=("observational_only",),
            )
        )
        plan_dict = input_data.get("causal_action_plan")
        plan = ActionPlan.from_dict(plan_dict) if isinstance(plan_dict, dict) else None
        document = to_report_document(
            readout=readout,
            contract=contract,
            qa_report=qa,
            action_plan=plan,
        )
        doc_dict = document.to_dict()
        return ToolResult(
            content=(
                "ReportDocument 已构建(FINDING 紧跟 CAVEAT,中性措辞)。"
                "把它传给 html_report(document=...) 经 QA 闸渲染。"
            ),
            metadata={"document": doc_dict},
        )
