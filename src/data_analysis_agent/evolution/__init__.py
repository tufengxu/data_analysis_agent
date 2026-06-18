"""Offline self-evolution pipeline: synthesize candidate skills, evaluate, promote.

Top-level sink — depends downward (telemetry / skills / agent_loop / protocol)
and is imported by nothing in the core. Runs only via ``python -m
data_analysis_agent.evolution``, never inside the interactive loop.
"""

from __future__ import annotations

from .synthesizer import (
    Cluster,
    SkillSynthesizer,
    cluster_uncovered,
    is_eligible,
    keywords,
    load_corpus,
)

__all__ = [
    "Cluster",
    "SkillSynthesizer",
    "cluster_uncovered",
    "is_eligible",
    "keywords",
    "load_corpus",
]
