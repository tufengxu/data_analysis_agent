"""JointAnalysisSkill: the on-demand recipe for multi-file / multi-sheet joins."""

from data_analysis_agent.runtime import build_skill_registry
from data_analysis_agent.skills.builtin import JointAnalysisSkill


def test_metadata_targets_joins_in_both_languages():
    skill = JointAnalysisSkill()
    assert skill.name == "joint_analysis"
    kw_blob = " ".join(skill.keywords).lower()
    assert "join" in kw_blob or "merge" in kw_blob
    # Chinese routing terms for the headline scenario
    assert any(term in skill.keywords for term in ("联合分析", "多表", "多文件", "关联", "合并"))


def test_instructions_cover_the_discover_then_merge_workflow():
    instr = JointAnalysisSkill().instructions
    assert "data_profile" in instr
    low = instr.lower()
    assert "join" in low or "merge" in low
    # must remind to validate the join (row counts / unmatched keys) — the
    # classic silent-failure mode of merges
    assert "row" in low or "key" in low


def test_allowed_tools_do_not_strip_profiling_or_reporting():
    tools = set(JointAnalysisSkill().allowed_tools)
    # active-skill allowlist narrows the pool, so it must keep what joins need
    assert {"data_profile", "python_analysis"} <= tools
    assert {"visualization", "html_report"} <= tools


def test_registered_as_builtin():
    assert build_skill_registry().get("joint_analysis") is not None
