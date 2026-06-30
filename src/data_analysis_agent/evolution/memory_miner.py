"""MemoryMiner: distill domain memory from trajectories (closes the L1 write-back).

Offline only — mirrors SkillSynthesizer: a deterministic eligibility filter plus
the single LLM step (extraction) injected as a callable, so the core needs no
protocol dependency and tests need no network.

Guardrails:
* only COMPLETED turns without negative feedback (reuses ``is_eligible``);
* mined metric_definitions are written UNCONFIRMED — light-confirm pending,
  so a wrong inference cannot silently become trusted (ADR 0004);
* (kind, key) upsert dedups re-mined facts and preserves accumulated trust;
* an extractor that raises on one turn never sinks the batch.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from ..memory.model import MemoryEntry, MemoryKind
from ..memory.store import MemoryStore
from .synthesizer import is_eligible, load_corpus

logger = logging.getLogger(__name__)

# A turn record -> zero or more candidate memories: {kind, key, content}.
ExtractFn = Callable[[dict[str, Any]], list[dict[str, Any]]]

_VALID_KINDS = {"metric_definition", "analysis_pref", "open_concern"}

# A metric definition can surface in a short turn, so mining is more lenient
# than skill synthesis (which needs a multi-step recipe to be worth distilling).
MIN_MODEL_TURNS = 1


class MemoryMiner:
    """Drives trajectory -> domain-memory extraction and writes the entries."""

    def __init__(
        self,
        trajectories_dir: str | Path,
        memory_store: MemoryStore,
        extract_fn: ExtractFn,
        *,
        min_model_turns: int = MIN_MODEL_TURNS,
    ) -> None:
        self.trajectories_dir = Path(trajectories_dir)
        self.memory = memory_store
        self.extract_fn = extract_fn
        self.min_model_turns = min_model_turns

    def mine(self) -> list[MemoryEntry]:
        """Extract + persist memory from every eligible turn; return what was written."""
        written: list[MemoryEntry] = []
        for turn in load_corpus(self.trajectories_dir):
            if not is_eligible(turn, min_model_turns=self.min_model_turns):
                continue
            try:
                candidates = self.extract_fn(turn) or []
            except Exception as e:  # noqa: BLE001 — one bad turn must not sink the batch
                logger.warning("memory extract failed on turn %s: %r", turn.get("turn_id"), e)
                continue
            for candidate in candidates:
                entry = self._validate(candidate, turn)
                if entry is not None:
                    self.memory.put(entry)
                    written.append(entry)
        return written

    @staticmethod
    def _validate(candidate: Any, turn: dict[str, Any]) -> MemoryEntry | None:
        """Coerce one extractor output into a MemoryEntry, or drop it."""
        if not isinstance(candidate, dict):
            return None
        kind = candidate.get("kind")
        key = candidate.get("key")
        content = candidate.get("content")
        if kind not in _VALID_KINDS or not isinstance(key, str) or not isinstance(content, str):
            return None
        key, content = key.strip(), content.strip()
        if not key or not content:
            return None
        return MemoryEntry(
            kind=cast(MemoryKind, kind),
            key=key,
            content=content,
            source_session=str(turn.get("session_id", "")),
            # Inferred metrics start unconfirmed (light-confirm); prefs/concerns
            # are lower-stakes and trusted as advisory by default.
            confirmed=(kind != "metric_definition"),
        )


__all__ = ["ExtractFn", "MemoryMiner"]
