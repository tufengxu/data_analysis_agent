"""CausalQATool:对 CausalContract 做因果就绪 QA(只读)。

薄封装 ``causal.qa.run_causal_qa``:返回确定性 6 态就绪 + 闭词汇 finding。核心不变量——
观察性/相关永远到不了 EXPERIMENT_READY;未就绪不得给因果结论。
"""

from __future__ import annotations

from typing import Any

from data_analysis_agent.causal.model import CausalContract
from data_analysis_agent.causal.qa import run_causal_qa

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class CausalQATool(Tool):
    """Run deterministic causal-readiness QA on a Causal Contract (read-only)."""

    @property
    def name(self) -> str:
        return "causal_qa"

    @property
    def description(self) -> str:
        return (
            "Run causal-readiness QA on a causal_contract. Returns a deterministic readiness "
            "(not_causal/blocked/needs_assumptions/needs_data/assumption_ready/experiment_ready) "
            "and closed-vocabulary findings. Observational/correlation evidence can NEVER reach "
            "experiment_ready. Do not draw causal conclusions unless experiment_ready. Read-only."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "causal_contract": {
                    "type": "object",
                    "description": "The causal_contract tool output.",
                },
            },
            "required": ["causal_contract"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        contract = input_data.get("causal_contract")
        if not isinstance(contract, dict):
            return ValidationResult.fail(
                "causal_contract is required and must be the contract object"
            )
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        contract = CausalContract.from_dict(input_data["causal_contract"])
        report = run_causal_qa(contract)
        return ToolResult(content=_render(report), metadata={"causal_qa": report.to_dict()})


def _render(report: Any) -> str:
    lines = [f"readiness: {report.readiness.value}"]
    for f in report.findings:
        lines.append(f"[{f.severity}] {f.code}: {f.message}")
    return "\n".join(lines)
