"""Domain memory (L1 self-evolution): dataset profiles + textual knowledge.

Wired into the agent loop only through plain callbacks (memory_injector /
memory_recorder), so the loop never imports this package.
"""

from __future__ import annotations

from .injector import MemoryInjector, render_profile
from .model import CONFIRM_AFTER_USES, DatasetProfile, MemoryEntry, column_fingerprint
from .profiler import ProfileStore, assess, build_profile, is_tabular
from .store import MemoryStore

__all__ = [
    "CONFIRM_AFTER_USES",
    "DatasetProfile",
    "MemoryEntry",
    "MemoryInjector",
    "MemoryStore",
    "ProfileStore",
    "assess",
    "build_profile",
    "column_fingerprint",
    "is_tabular",
    "render_profile",
]
