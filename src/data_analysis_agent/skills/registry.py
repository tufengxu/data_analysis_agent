"""Skill registry with static registration and dynamic discovery."""

from __future__ import annotations

import re

from .base import Skill

# CJK has no word spaces, so str.split() leaves a whole Chinese query as one
# un-matchable chunk. Shingling each run into overlapping 2-grams gives the
# query-token routing path something to match (留存分析 → 留存, 存分, 分析).
# Defined locally on purpose — see ADR 0006: the project's text matchers use
# deliberately different tokenizers and are NOT unified behind a shared util.
_CJK_RUN = re.compile(r"[一-鿿]+")


def _cjk_bigrams(text: str) -> set[str]:
    """Overlapping 2-grams of every CJK run in ``text`` (empty for pure ASCII)."""
    grams: set[str] = set()
    for run in _CJK_RUN.findall(text):
        grams.update(run[i : i + 2] for i in range(len(run) - 1))
    return grams


class SkillRegistry:
    """Registry for managing skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill

    def unregister(self, name: str) -> None:
        """Remove a skill by name."""
        self._skills.pop(name, None)

    def get(self, name: str) -> Skill | None:
        """Get a skill by exact name."""
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        """List all registered skills."""
        return list(self._skills.values())

    def find_by_keyword(self, keyword: str) -> list[Skill]:
        """Find skills matching a keyword in name or description."""
        keyword_lower = keyword.lower()
        results = []
        for skill in self._skills.values():
            text = self._routing_text(skill)
            if keyword_lower in text or any(keyword_lower in kw.lower() for kw in skill.keywords):
                results.append(skill)
        return results

    def match_best(self, query: str) -> Skill | None:
        """Simple keyword-based best match."""
        query_lower = query.lower()
        terms = query_lower.split()
        # CJK bigrams, deduped against the split terms so a 2-char run the split
        # already produced is never double-scored. Empty for pure ASCII, which
        # keeps Latin routing byte-identical to the prior split-only behavior.
        cjk_terms = _cjk_bigrams(query_lower) - set(terms)
        best: Skill | None = None
        best_score = 0

        for skill in self._skills.values():
            score = 0
            text = self._routing_text(skill)
            for phrase in skill.keywords:
                if phrase.lower() in query_lower:
                    score += 3
            for kw in terms:
                if kw in text:
                    score += 1
            for kw in cjk_terms:
                if kw in text:
                    score += 1
            if score > best_score:
                best_score = score
                best = skill

        return best if best_score > 0 else None

    def _routing_text(self, skill: Skill) -> str:
        """Text used for lightweight deterministic skill routing."""
        return (skill.name + " " + skill.description + " " + " ".join(skill.keywords)).lower()
