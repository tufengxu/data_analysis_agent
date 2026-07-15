"""SkillSynthesizer: distill recurring uncovered tasks into candidate skills.

Offline only — never runs in the interactive loop. The pipeline is deterministic
and testable (eligibility → cluster), with the one LLM step (reflection) injected
as a callable so the core needs no protocol dependency and tests need no network.

Guardrails against over-fitting (a real risk in data analysis — one successful
analysis does not make a general recipe):
* only COMPLETED turns with no negative feedback and >= MIN_MODEL_TURNS,
* only tasks NOT already covered by an active skill (uncovered = the gap worth
  filling),
* only clusters seen >= MIN_OCCURRENCES times.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..skills.loader import save_skill
from ..telemetry.trajectory import attach_feedback_to_turns, load_turns

# A reflection turns a cluster of similar turn-records into a skill record dict.
ReflectFn = Callable[[list[dict[str, Any]]], dict[str, Any] | None]

MIN_OCCURRENCES = 3
MIN_MODEL_TURNS = 4
_SIM_THRESHOLD = 0.4

_LATIN = re.compile(r"[A-Za-z0-9_]+")
_CJK_RUN = re.compile(r"[一-鿿]+")
_STOPWORDS = {
    "分析",
    "数据",
    "帮我",
    "一下",
    "这个",
    "那个",
    "给我",
    "看看",
    "看下",
    "请",
    "the",
    "a",
    "an",
    "of",
    "to",
    "me",
    "my",
    "please",
    "analyze",
    "analysis",
    "data",
    "show",
}


def keywords(text: str) -> set[str]:
    """Content features for clustering: latin words + CJK bigrams.

    CJK has no word boundaries, so a run is shingled into overlapping 2-grams
    (e.g. 留存分析 → 留存, 存分, 分析). Stopword bigrams like 分析/数据 are
    dropped; recurring task terms like 留存 survive and align across turns.
    """
    out: set[str] = set()
    for tok in _LATIN.findall(text):
        low = tok.lower()
        if low not in _STOPWORDS and len(low) >= 2:
            out.add(low)
    for run in _CJK_RUN.findall(text):
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in _STOPWORDS:
                out.add(bigram)
    return out


@dataclass
class Cluster:
    terms: list[str]
    turns: list[dict[str, Any]] = field(default_factory=list)


def is_eligible(turn: dict[str, Any], *, min_model_turns: int = MIN_MODEL_TURNS) -> bool:
    """A turn worth learning from: completed, not disliked, non-trivial.

    `model_turns` is the tool-iteration count (not model-call count), so a
    pure-chat turn with 0 iterations is correctly excluded as trivial.
    """
    if turn.get("terminal_reason") != "COMPLETED":
        return False
    if int(turn.get("model_turns", 0)) < min_model_turns:
        return False
    feedback = turn.get("feedback")
    return not (isinstance(feedback, dict) and feedback.get("kind") in ("bad", "rephrase"))


def cluster_uncovered(
    turns: list[dict[str, Any]], *, min_occurrences: int = MIN_OCCURRENCES
) -> list[Cluster]:
    """Greedy keyword clustering over tasks with NO active skill (the gaps)."""
    uncovered = [(t, keywords(str(t.get("user_input", "")))) for t in turns]
    uncovered = [(t, kw) for t, kw in uncovered if not t.get("active_skill") and kw]

    clusters: list[tuple[set[str], Cluster]] = []
    for turn, kw in uncovered:
        placed = False
        for seen, cluster in clusters:
            if _overlap(seen, kw) >= _SIM_THRESHOLD:
                cluster.turns.append(turn)
                seen |= kw
                placed = True
                break
        if not placed:
            clusters.append((set(kw), Cluster(terms=sorted(kw), turns=[turn])))
    return [
        Cluster(terms=sorted(seen), turns=c.turns)
        for seen, c in clusters
        if len(c.turns) >= min_occurrences
    ]


def _overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient |a∩b| / min(|a|,|b|).

    More robust than Jaccard here: shingling CJK runs into 2-grams produces many
    cross-word-boundary tokens that dilute the union, so Jaccard understates the
    similarity of tasks that genuinely share their core terms (留存, cohort).
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def load_corpus(trajectories_dir: str | Path) -> list[dict[str, Any]]:
    """All turn records across every session file, with feedback merged in."""
    d = Path(trajectories_dir)
    if not d.exists():
        return []
    corpus: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.jsonl")):
        turns = load_turns(path)
        attach_feedback_to_turns(turns, path)
        corpus.extend(turns)
    return corpus


class SkillSynthesizer:
    """Drives the synthesis pipeline and writes candidate skill records."""

    def __init__(
        self,
        trajectories_dir: str | Path,
        skills_dir: str | Path,
        reflect_fn: ReflectFn,
        *,
        min_occurrences: int = MIN_OCCURRENCES,
        min_model_turns: int = MIN_MODEL_TURNS,
    ) -> None:
        self.trajectories_dir = Path(trajectories_dir)
        self.skills_dir = Path(skills_dir)
        self.reflect_fn = reflect_fn
        self.min_occurrences = min_occurrences
        self.min_model_turns = min_model_turns

    def find_clusters(self) -> list[Cluster]:
        corpus = load_corpus(self.trajectories_dir)
        eligible = [t for t in corpus if is_eligible(t, min_model_turns=self.min_model_turns)]
        return cluster_uncovered(eligible, min_occurrences=self.min_occurrences)

    def synthesize(self) -> list[Path]:
        """Reflect each qualifying cluster into a candidate skill file."""
        written: list[Path] = []
        for cluster in self.find_clusters():
            record = self.reflect_fn(cluster.turns)
            if not isinstance(record, dict) or not record.get("name"):
                continue
            if not isinstance(record.get("instructions"), str):
                continue
            record["status"] = "candidate"
            record.setdefault("origin", "synthesized")
            record["source_trajectories"] = [str(t.get("turn_id", "")) for t in cluster.turns]
            path = save_skill(self.skills_dir, record)
            if path is not None:  # None = rejected for prompt-injection markers
                written.append(path)
        return written
