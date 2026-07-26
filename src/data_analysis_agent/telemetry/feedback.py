"""Feedback signals for trajectories — explicit (user-stated) and implicit.

Implicit signals are heuristic and noisy by design: Stage A only *collects*
them; consumption (synthesizer / evaluator) filters via human review. Keeping
the heuristics here, pure and testable, means the noise model is one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

FeedbackKind = Literal["good", "bad", "interrupted", "rephrase"]

# CJK correction/negation markers — matched as SUBSTRINGS (CJK has no word
# boundaries). Kept to UNAMBIGUOUS corrections (negation / error / redo + a few
# specific 改/换 bigrams) so ordinary queries don't trip them. Ambiguous openers
# like 等等(list terminator "A、B等等")/应该是(neutral hypothesis)/再算(scheduling)
# are deliberately excluded — a real correction usually also carries a clearer
# marker (不对/错了/重新) in the same utterance.
_CJK_NEGATION_MARKERS = (
    "不对",
    "不是",
    "不准确",
    "不正确",
    "不可以",
    "不行",
    "错了",
    "错的",
    "有错",
    "重新",
    "重来",
    "重做",
    "重算",
    "再试",
    "改一",
    "再改",
    "换个",
)

# English correction markers — matched with WORD BOUNDARIES so a bare "no"
# doesn't hit "note/know/now" and "again" doesn't hit "against".
_ENGLISH_NEGATION_MARKERS = (
    "no",
    "nope",
    "wrong",
    "redo",
    "again",
    "try again",
    "not right",
    "not what",
    "that's wrong",
    "that is wrong",
    "wait no",
)
_ENGLISH_NEGATION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in _ENGLISH_NEGATION_MARKERS) + r")\b"
)

REPHRASE_GAP_SECONDS = 60.0


@dataclass(frozen=True)
class FeedbackRecord:
    """A feedback signal attached to a turn."""

    kind: FeedbackKind
    detail: str = ""
    implicit: bool = False


def looks_like_rephrase(next_input: str, gap_seconds: float) -> bool:
    """Heuristic: a fast follow-up containing a negation/correction marker.

    Both conditions required — a slow follow-up, or a neutral one, is not
    counted (keeps the false-positive rate down on this deliberately crude rule).
    CJK markers match by substring; English markers by word boundary so short
    tokens like "no"/"again" don't fire inside unrelated words.
    """
    if gap_seconds > REPHRASE_GAP_SECONDS:
        return False
    text = next_input.lower()
    if any(marker in text for marker in _CJK_NEGATION_MARKERS):
        return True
    return bool(_ENGLISH_NEGATION_RE.search(text))


def parse_explicit_feedback(user_input: str) -> FeedbackRecord | None:
    """Parse a leading ``/good`` or ``/bad`` slash command into feedback.

    Returns None for ordinary input so the caller can route it as a normal turn.
    """
    stripped = user_input.strip()
    for marker, kind in (("/good", "good"), ("/bad", "bad")):
        if stripped == marker or stripped.startswith(marker + " "):
            detail = stripped[len(marker) :].strip()
            return FeedbackRecord(kind=kind, detail=detail, implicit=False)  # type: ignore[arg-type]
    return None
