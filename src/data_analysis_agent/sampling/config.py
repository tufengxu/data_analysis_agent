"""Configuration for the sampling-based compaction module.

A single ``fidelity_level`` knob maps to sample size, top-k, and the quantile
set, per the research's compression-vs-fidelity tradeoff. All fields have
defaults so the config is optional everywhere (backward compatible).
"""

from __future__ import annotations

from dataclasses import dataclass

FIDELITY_LEVELS = ("low", "mid", "high")


@dataclass(frozen=True)
class SamplingConfig:
    """Knobs for representative sampling + statistical summary.

    Attributes:
        trigger_chars: Results larger than this (chars) get summarized; below,
            they pass through unchanged (~2k tokens at 4 chars/token).
        fidelity_level: one of ``low`` / ``mid`` / ``high``.
        max_sample_rows: number of representative detail rows to keep.
        top_k: number of high-frequency values to keep per categorical column.
        quantiles: quantile probabilities to report for numeric columns.
        stratify: ``auto`` stratifies by a low-cardinality categorical column
            when one exists; ``none`` always uses simple reservoir sampling.
        include_outliers: append IQR-flagged outlier rows to the summary.
        max_outlier_rows: cap on appended outlier rows.
        seed: deterministic sampling seed.
        trigger_rows: in the sandbox, a ``result`` DataFrame with more rows than
            this is auto-summarized instead of printed in full.
    """

    trigger_chars: int = 8000
    fidelity_level: str = "mid"
    max_sample_rows: int = 20
    top_k: int = 10
    quantiles: tuple[float, ...] = (0.01, 0.25, 0.5, 0.75, 0.99)
    stratify: str = "auto"
    include_outliers: bool = True
    max_outlier_rows: int = 5
    seed: int = 0
    trigger_rows: int = 50
    # Compression-gain gating (pressure-adaptive): a summary replaces the
    # original only if it is shorter than original * accept_ratio, where
    # accept_ratio interpolates from low_pressure (context empty → strict) to
    # high_pressure (context near full → lenient) by context_pressure.
    gate_ratio_low_pressure: float = 0.65
    gate_ratio_high_pressure: float = 0.90

    @classmethod
    def for_fidelity(cls, level: str) -> SamplingConfig:
        """Build a config preset for a fidelity level."""
        if level == "low":
            return cls(
                fidelity_level="low",
                max_sample_rows=10,
                top_k=5,
                quantiles=(0.05, 0.5, 0.95),
            )
        if level == "mid":
            return cls(fidelity_level="mid")
        if level == "high":
            return cls(
                fidelity_level="high",
                max_sample_rows=40,
                top_k=20,
                quantiles=(0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99),
            )
        raise ValueError(f"unknown fidelity level: {level!r} (expected one of {FIDELITY_LEVELS})")
