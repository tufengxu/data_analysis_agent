"""Tests for Stage C: data-driven skill loading + builtin migration parity."""

import pytest

from data_analysis_agent.skills.base import Skill
from data_analysis_agent.skills.builtin import (
    CorrelationAnalysisSkill,
    DescriptiveAnalysisSkill,
    ReportGenerationSkill,
    TrendAnalysisSkill,
)
from data_analysis_agent.skills.loader import (
    DeclarativeSkill,
    load_skills,
    save_skill,
    skill_to_dict,
)
from data_analysis_agent.skills.registry import SkillRegistry

_BUILTINS = [
    DescriptiveAnalysisSkill(),
    CorrelationAnalysisSkill(),
    TrendAnalysisSkill(),
    ReportGenerationSkill(),
]


def test_declarative_skill_from_dict_roundtrip():
    record = {
        "name": "cohort_analysis",
        "description": "留存/同期群分析",
        "keywords": ["留存", "cohort"],
        "allowed_tools": ["read_file", "python_analysis"],
        "instructions": "1. 解析注册日期列\n2. 构建同期群矩阵",
        "status": "active",
        "origin": "synthesized",
    }
    skill = DeclarativeSkill.from_dict(record)
    assert skill.name == "cohort_analysis"
    assert skill.keywords == ["留存", "cohort"]
    assert skill.to_dict()["instructions"] == record["instructions"]


def test_declarative_skill_rejects_incomplete_record():
    with pytest.raises(ValueError):
        DeclarativeSkill.from_dict({"name": "x"})  # no instructions


@pytest.mark.parametrize("builtin", _BUILTINS, ids=[b.name for b in _BUILTINS])
def test_builtin_migrates_to_declarative_with_parity(builtin: Skill):
    """Every builtin must round-trip to a DeclarativeSkill with identical behavior
    (this is the format contract the synthesizer's output must satisfy)."""
    declarative = DeclarativeSkill.from_dict(skill_to_dict(builtin))
    assert declarative.name == builtin.name
    assert declarative.description == builtin.description
    assert declarative.keywords == builtin.keywords
    assert declarative.allowed_tools == builtin.allowed_tools
    assert declarative.instructions == builtin.instructions


async def test_declarative_skill_execute_marker():
    skill = DeclarativeSkill(
        name="x", description="d", instructions="i", allowed_tools=["read_file"]
    )
    result = await skill.execute("分析", {})
    assert "x skill activated" in result.output
    assert result.tools_used == ["read_file"]


def test_save_and_load_active_only(tmp_path):
    save_skill(tmp_path, skill_to_dict(DescriptiveAnalysisSkill(), status="active"))
    save_skill(
        tmp_path,
        {
            "name": "candidate_skill",
            "description": "draft",
            "instructions": "do x",
            "status": "candidate",
        },
    )

    active = load_skills(tmp_path, statuses=("active",))
    assert [s.name for s in active] == ["descriptive_analysis"]

    both = load_skills(tmp_path, statuses=("active", "candidate"))
    assert {s.name for s in both} == {"descriptive_analysis", "candidate_skill"}


def test_load_skills_skips_malformed(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "incomplete.json").write_text('{"name": "x"}', encoding="utf-8")
    assert load_skills(tmp_path) == []


def test_build_skill_registry_loads_active_declarative(tmp_path):
    from data_analysis_agent.__main__ import build_skill_registry

    save_skill(
        tmp_path,
        {
            "name": "cohort_analysis",
            "description": "留存分析",
            "keywords": ["留存", "同期群"],
            "allowed_tools": ["read_file", "python_analysis"],
            "instructions": "构建同期群矩阵",
            "status": "active",
        },
    )
    registry = build_skill_registry(tmp_path)
    # Builtins still present, plus the loaded declarative skill is routable.
    assert registry.get("descriptive_analysis") is not None
    matched = registry.match_best("帮我做留存分析")
    assert matched is not None and matched.name == "cohort_analysis"


def test_candidate_skills_not_loaded_into_live_registry(tmp_path):
    from data_analysis_agent.__main__ import build_skill_registry

    save_skill(
        tmp_path,
        {"name": "risky", "description": "d", "instructions": "i", "status": "candidate"},
    )
    registry = build_skill_registry(tmp_path)
    assert registry.get("risky") is None  # candidates stay out until promoted


def test_registry_accepts_declarative_skill():
    reg = SkillRegistry()
    reg.register(DeclarativeSkill(name="x", description="d", instructions="i", keywords=["foo"]))
    assert reg.match_best("foo please") is not None


# --- CJK routing: the str.split() blind spot (ADR 0006) ----------------------


def test_cjk_query_routes_via_description_bigrams_not_just_keywords():
    """A pure-Chinese query whose term lives in the skill's DESCRIPTION (not its
    declared keywords) used to route to nothing: str.split() made the whole query
    one un-matchable chunk. CJK bigrams recover it."""
    reg = SkillRegistry()
    # 留存 appears only in the description; the declared keyword deliberately does
    # NOT occur in the query, so the phrase path scores zero.
    reg.register(
        DeclarativeSkill(
            name="cohort", description="留存与同期群分析", instructions="i", keywords=["zzz"]
        )
    )
    reg.register(
        DeclarativeSkill(name="other", description="unrelated", instructions="i", keywords=["qqq"])
    )
    matched = reg.match_best("我想看留存情况")
    assert matched is not None and matched.name == "cohort"


def test_phrase_match_still_dominates_generic_cjk_bigram_noise():
    """A declared-keyword (phrase, +3) hit must beat a skill that only shares a
    generic description bigram (+1) — the fix must not let CJK noise outrank an
    explicit keyword route."""
    reg = SkillRegistry()
    specific = DeclarativeSkill(
        name="specific", description="趋势", instructions="i", keywords=["趋势变化"]
    )
    generic = DeclarativeSkill(
        name="generic", description="销售数据分析概览", instructions="i", keywords=["zzz"]
    )
    reg.register(specific)
    reg.register(generic)
    # Query contains the specific keyword 趋势变化 AND shares 销售/数据 bigrams with generic.
    matched = reg.match_best("帮我分析销售数据的趋势变化")
    assert matched is specific


def test_pure_ascii_routing_unchanged_by_cjk_path():
    """Pure-ASCII queries must be byte-identical to the split-only behavior:
    _cjk_bigrams is empty, so nothing is added or perturbed."""
    reg = SkillRegistry()
    reg.register(DescriptiveAnalysisSkill())
    reg.register(TrendAnalysisSkill())
    assert reg.match_best("descriptive distribution summary").name == "descriptive_analysis"
    assert reg.match_best("zzz totally unrelated") is None


def test_save_skill_disk_cap_evicts_retired_protects_active(tmp_path):
    """save_skill enforces the skills-dir cap: retired is evicted (rank 0) while
    an active skill — even one with older mtime — is protected."""
    import os
    import time

    def _touch(path, offset):
        st = path.stat()
        os.utime(path, (st.st_atime, time.time() + offset))

    active = save_skill(
        tmp_path, {"name": "active", "instructions": "A" * 100_000, "status": "active"}
    )
    _touch(active, -1000)  # oldest on disk, but must survive (protected)
    save_skill(tmp_path, {"name": "retired", "instructions": "R" * 100_000, "status": "retired"})
    # New candidate with a cap forcing exactly the retired file out: total
    # ~300KB, cap 250KB → evict one; retired (rank 0) beats candidate (rank 1).
    save_skill(
        tmp_path,
        {"name": "newcand", "instructions": "N" * 100_000, "status": "candidate"},
        max_dir_bytes=250_000,
    )
    names = {p.stem for p in tmp_path.glob("*.json")}
    assert "active" in names  # protected regardless of mtime
    assert "retired" not in names  # evicted — rank 0
    assert "newcand" in names  # just-written, newest candidate survives


def test_save_skill_disk_cap_protects_proposed_promote(tmp_path):
    """proposed_promote skills (passed eval, awaiting human review) are protected
    from eviction, same as active."""
    save_skill(
        tmp_path,
        {"name": "promote", "instructions": "P" * 80_000, "status": "proposed_promote"},
    )
    save_skill(tmp_path, {"name": "ret", "instructions": "X" * 80_000, "status": "retired"})
    # total ~240KB, cap 200KB → evict one; retired out, promote + new kept.
    save_skill(
        tmp_path,
        {"name": "new", "instructions": "Y" * 80_000, "status": "candidate"},
        max_dir_bytes=200_000,
    )
    names = {p.stem for p in tmp_path.glob("*.json")}
    assert "promote" in names  # proposed_promote protected
    assert "ret" not in names  # retired evicted to fit cap
    assert "new" in names


def test_corrupt_skill_file_evicted_before_retired_and_candidate(tmp_path):
    """A corrupt/unreadable skill file (rank 0) is evicted before retired and
    candidate — pure dead weight goes first."""
    save_skill(tmp_path, {"name": "active", "instructions": "x" * 100_000, "status": "active"})
    save_skill(tmp_path, {"name": "retired", "instructions": "x" * 100_000, "status": "retired"})
    # A large, malformed skill file (not written via save_skill).
    (tmp_path / "corrupt.json").write_text("x" * 100_000 + "{ not json", encoding="utf-8")
    # total ~400KB, cap 350KB → evict exactly one; corrupt (rank 0) beats retired (1).
    save_skill(
        tmp_path,
        {"name": "cand", "instructions": "x" * 100_000, "status": "candidate"},
        max_dir_bytes=350_000,
    )
    names = {p.stem for p in tmp_path.glob("*.json")}
    assert "active" in names
    assert "corrupt" not in names  # rank 0 → evicted first
    assert "retired" in names  # rank 1 → survived over corrupt
    assert "cand" in names
