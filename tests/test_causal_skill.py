"""causal_decision_analysis skill:属性、注册接线、关键词路由。"""

from __future__ import annotations

import pytest

from data_analysis_agent.runtime import build_skill_registry
from data_analysis_agent.skills.base import SkillResult
from data_analysis_agent.skills.builtin import DescriptiveAnalysisSkill
from data_analysis_agent.skills.causal_skill import CausalDecisionAnalysisSkill
from data_analysis_agent.skills.registry import SkillRegistry

_CAUSAL_TOOLS = {
    "causal_contract",
    "causal_qa",
    "experiment_readout",
    "causal_action_plan",
}


def test_skill_attributes():
    skill = CausalDecisionAnalysisSkill()
    assert skill.name == "causal_decision_analysis"
    assert _CAUSAL_TOOLS.issubset(set(skill.allowed_tools))
    assert "read_file" in skill.allowed_tools
    assert "python_analysis" in skill.allowed_tools
    assert "html_report" in skill.allowed_tools
    assert "因果" in skill.keywords
    assert "导致" in skill.keywords


def test_instructions_enforce_workflow_and_forbid_overclaiming():
    instr = CausalDecisionAnalysisSkill().instructions
    assert "experiment_ready" in instr
    assert "FORBIDDEN" in instr or "禁止" in instr
    # 工作流顺序:contract → qa → readout
    assert instr.index("causal_contract") < instr.index("causal_qa")
    assert instr.index("causal_qa") < instr.index("experiment_readout")


def test_skill_registered_in_build_skill_registry():
    registry = build_skill_registry()
    assert registry.get("causal_decision_analysis") is not None


async def test_execute_returns_skill_result():
    result = await CausalDecisionAnalysisSkill().execute("A/B 是否提升留存", {})
    assert isinstance(result, SkillResult)
    assert "causal" in result.output.lower()


def test_routing_prefers_causal_skill_for_causal_query():
    registry = SkillRegistry()
    registry.register(DescriptiveAnalysisSkill())
    registry.register(CausalDecisionAnalysisSkill())
    # 因果查询应路由到 causal skill,而非 descriptive
    matched = registry.match_best("A/B 实验组是否导致了留存提升?")
    assert matched is not None
    assert matched.name == "causal_decision_analysis"


def test_routing_does_not_match_causal_for_pure_descriptive_query():
    registry = SkillRegistry()
    registry.register(DescriptiveAnalysisSkill())
    registry.register(CausalDecisionAnalysisSkill())
    matched = registry.match_best("给我各列的描述性统计与分布")
    # 描述性查询不应路由到 causal skill
    assert matched is None or matched.name != "causal_decision_analysis"


@pytest.mark.parametrize(
    "query",
    [
        "实验组是否提高了 D7 留存?",
        "variant_b 是否提升 revenue",
        "这次活动是否导致收入提升?",
    ],
)
def test_routing_matches_various_causal_phrasings(query: str):
    registry = SkillRegistry()
    registry.register(CausalDecisionAnalysisSkill())
    assert registry.match_best(query) is not None
