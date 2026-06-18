"""Feedback signals for trajectories — explicit (user-stated) and implicit.

Implicit signals are heuristic and noisy by design: Stage A only *collects*
them; consumption (synthesizer / evaluator) filters via human review. Keeping
the heuristics here, pure and testable, means the noise model is one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FeedbackKind = Literal["good", "bad", "interrupted", "rephrase"]

# A next-turn message arriving fast and containing one of these is treated as a
# likely correction of the previous turn (implicit "bad"). Tunable, low-stakes.
_NEGATION_MARKERS = (
    "不对",
    "不是",
    "重新",
    "重来",
    "错了",
    "不行",
    "再试",
    "no",
    "wrong",
    "redo",
    "again",
    "not what",
)

REPHRASE_GAP_SECONDS = 60.0


@dataclass(frozen=True)
class FeedbackRecord:
    """A feedback signal attached to a turn."""

    kind: FeedbackKind
    detail: str = ""
    implicit: bool = False


def looks_like_rephrase(next_input: str, gap_seconds: float) -> bool:
    """Heuristic: a fast follow-up containing a negation marker = likely redo.

    Both conditions required — a slow follow-up, or a neutral one, is not
    counted (keeps the false-positive rate down on this deliberately crude rule).
    """
    if gap_seconds > REPHRASE_GAP_SECONDS:
        return False
    text = next_input.lower()
    return any(marker in text for marker in _NEGATION_MARKERS)


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
