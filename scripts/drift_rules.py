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
    {
        "who": "data_analysis_agent.kernel",
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
        "who": "data_analysis_agent.kernel.kernel_main",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.artifacts",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.jsonl_store",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.telemetry",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.protocol",
            "data_analysis_agent.security",
        ],
    },
    {
        "who": "data_analysis_agent.memory",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.protocol",
            "data_analysis_agent.security",
        ],
    },
    {
        # synthesizer stays protocol-free (reflection injected); only the
        # offline entry point evolution/__main__ may reach protocol.
        "who": "data_analysis_agent.evolution.synthesizer",
        "forbid": [
            "data_analysis_agent.protocol",
            "data_analysis_agent.agent_loop",
        ],
    },
    # The loop reaches telemetry/memory/evolution only through callbacks, never
    # by import — these rules enforce that decoupling as a standing invariant.
    {
        "who": "data_analysis_agent.agent_loop",
        "forbid": [
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.runtime",
        ],
    },
    # session may hold a TrajectoryLogger (telemetry) but not memory/evolution.
    {
        "who": "data_analysis_agent.session",
        "forbid": [
            "data_analysis_agent.evolution",
            "data_analysis_agent.memory",
            "data_analysis_agent.runtime",
        ],
    },
    {
        "who": "data_analysis_agent.tools",
        "forbid": [
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.runtime",
        ],
    },
    {"who": "data_analysis_agent.memory", "forbid": ["data_analysis_agent.evolution"]},
    {"who": "data_analysis_agent.telemetry", "forbid": ["data_analysis_agent.evolution"]},
    # The tool-authorization seam is depended-on BY agent_loop; importing back
    # would be circular and re-couple the seam it exists to decouple.
    {
        "who": "data_analysis_agent.security.tool_gate",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.runtime",
            "data_analysis_agent.session",
        ],
    },
    # The recovery-policy seam is likewise depended-on BY agent_loop.
    {
        "who": "data_analysis_agent.recovery",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.runtime",
            "data_analysis_agent.session",
        ],
    },
]

# Documents scanned for dead repo-path references.
DOC_FILES: list[str] = ["README.md", "AGENTS.md", "docs/ARCHITECTURE.md"]

# god-file warning threshold (lines of code). Phase 1: warn only.
FILE_SIZE_LIMIT = 600
