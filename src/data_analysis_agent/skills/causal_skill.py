"""CausalDecisionAnalysisSkill:路由因果/实验/行动请求到因果决策工作流。

instructions + allowed_tools + keywords 即技能本体(见 skills/loader 设计);execute 只返回
激活标记,实际工具编排由 agent_loop 驱动。强制工作流把"相关"与"因果"严格分离,禁止过度声称。
"""

from __future__ import annotations

from typing import Any

from .base import Skill, SkillResult


class CausalDecisionAnalysisSkill(Skill):
    """Route causal / experiment / action requests through the causal-decision workflow."""

    @property
    def name(self) -> str:
        return "causal_decision_analysis"

    @property
    def description(self) -> str:
        return (
            "Analyze causal and A/B experiment questions: separate descriptive / associational / "
            "experimental / causal-assumption claims, run causal-readiness QA, read out randomized "
            "experiments with bounded decisions, and produce evidence-tied action plans. Refuses to "
            "upgrade correlation to causation."
        )

    @property
    def instructions(self) -> str:
        return (
            "When handling a causal / experiment / action question:\n"
            "1. Run report_need to parse the request (keep explicit vs inferred requirements separate; "
            "never treat inferences as explicit facts)\n"
            "2. Run data_profile + report_context to capture candidate columns and business grain\n"
            "3. Run causal_contract to build the causal contract — gaps go to missing_context, NEVER "
            "guess treatment/outcome/assignment\n"
            "4. Run causal_qa to check readiness — do NOT draw causal conclusions unless "
            "experiment_ready; observational evidence can never reach experiment_ready\n"
            "5. If a randomized experiment: run experiment_readout (use python_analysis to pull the "
            "group/outcome columns from the file and pass them as records or columns)\n"
            "6. Otherwise label the output as correlational / hypothesis and ask for assumptions or an "
            "experiment design; do NOT report a causal effect number\n"
            "7. Run causal_action_plan for a bounded recommendation with mechanism / evidence / "
            "assumptions / monitoring / rollback\n"
            "8. Run causal_report to build a ReportDocument from the contract + qa + readout (+ "
            "action plan); it places a CAVEAT immediately after every causal FINDING and uses neutral "
            "phrasing (difference of / lift of / associated with) in findings, reserving causal "
            "language for caveat / assumption blocks\n"
            "9. Pass that `document` to html_report(document=...) so it goes through the QA gate; "
            "fix any blocker it lists. Do not hand-assemble causal blocks — use causal_report.\n"
            "10. FORBIDDEN: treating correlation as causation, and using LLM judgment as the sole "
            "causal-readiness gate\n"
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "causal",
            "causation",
            "cause",
            "effect",
            "uplift",
            "treatment",
            "outcome",
            "experiment",
            "a/b",
            "ab test",
            "variant",
            "control",
            "randomized",
            "因果",
            "导致",
            "影响",
            "归因",
            "实验组",
            "对照组",
            "随机",
            "分流",
            "ab测试",
        ]

    @property
    def allowed_tools(self) -> list[str]:
        return [
            "read_file",
            "data_profile",
            "report_need",
            "report_context",
            "causal_contract",
            "causal_qa",
            "experiment_readout",
            "causal_action_plan",
            "causal_report",
            "python_analysis",
            "html_report",
        ]

    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        return SkillResult(
            output=f"Causal decision analysis skill activated for: {query}",
            tools_used=["causal_contract", "causal_qa", "experiment_readout"],
        )
