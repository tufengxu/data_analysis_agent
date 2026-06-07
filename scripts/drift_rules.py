"""Data-driven rules for the deterministic drift checks.

Edit here to evolve architecture guarantees. ``who`` matches a module whose
dotted name equals it or starts with ``who + "."``; ``forbid`` lists dotted
prefixes that such modules must not import.
"""

from __future__ import annotations

IMPORT_RULES: list[dict[str, object]] = [
    {
        "who": "data_analysis_agent.sampling",
        "forbid": [
            "data_analysis_agent.tools",
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.skills",
            "data_analysis_agent.security",
            "data_analysis_agent.context",
        ],
    },
    {
        "who": "data_analysis_agent.sampling.sandbox_summary",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.tools",
        "forbid": ["data_analysis_agent.agent_loop"],
    },
    {
        "who": "data_analysis_agent.protocol",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
        ],
    },
]

# Documents scanned for dead repo-path references.
DOC_FILES: list[str] = ["README.md", "AGENTS.md", "docs/ARCHITECTURE.md"]

# god-file warning threshold (lines of code). Phase 1: warn only.
FILE_SIZE_LIMIT = 600
