"""Context management and compression system.

Five-level compression pipeline:
L1 Budget Reduction → L2 Snip → L3 Microcompact → L4 Context Collapse → L5 Auto-Compact
"""

from .compression import (
    AutoCompactStrategy,
    BudgetReductionStrategy,
    CompressionResult,
    ContextCollapseStrategy,
    ContextCompressor,
    MicrocompactStrategy,
    SnipStrategy,
    estimate_tokens,
)

__all__ = [
    "AutoCompactStrategy",
    "BudgetReductionStrategy",
    "CompressionResult",
    "ContextCollapseStrategy",
    "ContextCompressor",
    "MicrocompactStrategy",
    "SnipStrategy",
    "estimate_tokens",
]
