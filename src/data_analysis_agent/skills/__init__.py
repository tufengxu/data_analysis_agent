"""Skill system for domain-specific analysis capabilities.

Skills encapsulate reusable analysis workflows (e.g., user retention analysis,
anomaly detection, A/B test evaluation). Each Skill includes tool templates,
parameter schemas, domain prompt fragments, and permission constraints.
"""

from .base import Skill, SkillResult
from .builtin import (
    CorrelationAnalysisSkill,
    DescriptiveAnalysisSkill,
    TrendAnalysisSkill,
)
from .registry import SkillRegistry

__all__ = [
    "Skill",
    "SkillResult",
    "SkillRegistry",
    "DescriptiveAnalysisSkill",
    "CorrelationAnalysisSkill",
    "TrendAnalysisSkill",
]
