"""Configuration management for the data analysis agent.

Loads from:
1. Default values
2. Config file (JSON/YAML)
3. Environment variables
4. CLI arguments
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass
class AgentConfig:
    """Runtime configuration for the agent."""

    # LLM settings
    model: str = "claude-sonnet-4-6-20260401"
    api_key: str = ""
    max_tokens: int = 8192
    max_turns: int = 15

    # System prompt
    system_prompt: str = (
        "You are a data analysis assistant. You can read files, execute Python code, "
        "query data sources, generate visualizations, and produce H5 HTML analysis "
        "reports with ECharts charts (html_report). Classify each request before acting: "
        "answer simple questions directly, use one tool directly for simple single-tool tasks, "
        "and write a concise plan before executing complex multi-step tasks. "
        "When a matching skill is active, follow that skill before generic reasoning or tools.\n"
        "You analyse local data files: CSV/TSV, Excel (.xlsx/.xls, possibly multi-sheet) "
        "and Parquet. Before writing analysis code, call data_profile on the file or "
        "directory to discover its sheets, columns and dtypes (and, across files, the "
        "shared keys to join on). Pass the ABSOLUTE paths it reports into pd.read_csv / "
        "pd.read_excel — relative paths do not resolve in the execution sandbox. "
        "Then call data_quality on a file to surface missingness, duplicate rows, "
        "constant columns, numeric outliers and type anomalies before you trust the data. "
        "For multi-table or multi-sheet work, call join_planner on the paths to get "
        "candidate join keys, relationship types (1:1/1:N/N:1/N:N), row-multiplication "
        "risk and a safe join order before merging. "
        "Before computing a named metric, call metric_contract to pin down its口径 "
        "(numerator/denominator/aggregation, filters, exclusions, time window, grain, "
        "timezone) and catch incomplete or memory-mismatched definitions. "
        "For multi-sheet or multi-file work: profile each source, decide the join keys, "
        "then merge/concat in python_analysis; the kernel keeps state across calls, so "
        "load each table once and reuse the variables.\n"
        "Report delivery workflow: for any report/汇报 request, run report_need → "
        "report_context → report_contract BEFORE rendering, then build a ReportDocument "
        "(executive summary first, findings with evidence_refs, charts via chart_render, "
        "recommendations, caveats, data_scope) and call html_report with that `document`. "
        "The QA gate REFUSES a DRAFT report (missing contract / executive summary / data_scope "
        "/ chart spec) — fix the blockers it lists. Do not call the legacy title/sections form "
        "for business reports; it skips the QA gate. Hard rule: no contract, no render."
    )

    # Tool settings
    python_timeout: int = 30
    python_memory_mb: int = 512
    max_result_chars: int = 50_000

    # Permission settings
    permission_mode: str = "default"  # default | plan | auto | bypass
    deny_patterns: list[str] = field(default_factory=list)
    # Named permission preset (overrides mode/deny when set): "" | local_safe | local_dev.
    # local_safe = read-only allow, known mutators ask, unknown deny (Web default).
    # local_dev  = CLI-friendly, no engine, everything allowed (today's default).
    permission_preset: str = ""

    # Context management
    context_budget_tokens: int = 180_000
    enable_compression: bool = True

    # Result sampling / compaction
    sampling_trigger_chars: int = 8000
    sampling_fidelity: str = "mid"  # low | mid | high

    # Result store (CCR-lite)
    result_store_ttl_seconds: int = 3600
    result_store_max_total_mb: int = 64
    result_store_max_entry_mb: int = 8

    # Persistent analysis kernel (state survives across python_analysis calls).
    # Disable to force the stateless one-shot sandbox per call.
    persistent_kernel: bool = True

    # ECharts source for HTML reports: an http(s) URL becomes a <script src>
    # tag; a local file path is inlined for fully-offline reports.
    echarts_src: str = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"

    # Self-evolution: record session trajectories (the corpus everything learns
    # from). Off → no trajectory files, no memory extraction input.
    enable_telemetry: bool = True

    # Domain memory: inject dataset profiles / metric definitions / prefs into
    # the system prompt and capture profiles on tabular reads.
    enable_memory: bool = True
    memory_inject_budget_tokens: int = 1500

    # Trajectory tool-input capture: record the "success process" (tool params /
    # code skeletons) so the synthesizer can learn reusable recipes. Off → only
    # tool name / duration / result_chars are recorded (privacy-preserving).
    enable_trajectory_inputs: bool = True

    # Sensitive mode: opt-in per-run privacy switch. Forces enable_memory=False
    # and enable_trajectory_inputs=False AND suppresses the session store + the
    # run manifest's request field, so nothing user-input-shaped is written to
    # ~/.daa this run (telemetry still records tool name/duration). Covers user-
    # input paths only — computed tool output (ResultStore/artifacts) may still
    # echo input data and is redaction-pending; see docs/roadmap/backlog.md.
    sensitive_mode: bool = False

    def artifacts_dir(self, persist_path: str | Path | None = None) -> Path:
        """Directory for user-facing artifacts (charts); follows persist_path."""
        import tempfile

        if persist_path:
            path = Path(persist_path).expanduser().resolve().parent / "artifacts"
        else:
            path = Path(tempfile.mkdtemp(prefix="daa_artifacts_"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def kernel_work_dir(self, persist_path: str | Path | None = None) -> Path:
        """Session workspace for the persistent kernel; follows persist_path."""
        import tempfile

        if persist_path:
            path = Path(persist_path).expanduser().resolve().parent / "workspace"
            path.mkdir(parents=True, exist_ok=True)
            return path
        return Path(tempfile.mkdtemp(prefix="daa_kernel_"))

    def daa_home(self) -> Path:
        """Root for cross-session evolution assets (trajectories, memory, skills)."""
        return Path(os.environ.get("DAA_HOME", str(Path.home() / ".daa")))

    def trajectories_dir(self) -> Path:
        return self._evolution_subdir("trajectories")

    def memory_dir(self) -> Path:
        return self._evolution_subdir("memory")

    def skills_dir(self) -> Path:
        return self._evolution_subdir("skills")

    def eval_tasks_dir(self) -> Path:
        """Root for harvested eval tasks + fixtures (~/.daa/eval_tasks)."""
        return self._evolution_subdir("eval_tasks")

    def _evolution_subdir(self, name: str) -> Path:
        """Return a ~/.daa/<name> path, creating it best-effort.

        mkdir is suppressed (not asserted): a read-only DAA_HOME must not crash
        startup — the store layers re-check writability and degrade gracefully.
        """
        path = self.daa_home() / name
        with contextlib.suppress(OSError):
            path.mkdir(parents=True, exist_ok=True)
        return path

    def result_store(self, persist_path: str | Path | None = None) -> Any:
        """Build a ResultStore; dir follows persist_path (else a tempdir)."""
        import tempfile

        from .sampling.result_store import ResultStore

        if persist_path:
            store_dir = Path(persist_path).expanduser().resolve().parent / "results"
        else:
            store_dir = Path(tempfile.mkdtemp(prefix="daa_results_"))
        return ResultStore(
            store_dir,
            ttl_seconds=self.result_store_ttl_seconds,
            max_total_bytes=self.result_store_max_total_mb * 1024 * 1024,
            max_entry_bytes=self.result_store_max_entry_mb * 1024 * 1024,
        )

    def sampling_config(self) -> Any:
        """Build a SamplingConfig from the fidelity preset + trigger override."""
        from .sampling import SamplingConfig

        base = SamplingConfig.for_fidelity(self.sampling_fidelity)
        return replace(base, trigger_chars=self.sampling_trigger_chars)

    @classmethod
    def from_env(cls) -> AgentConfig:
        """Load configuration from environment variables."""
        config = cls()
        config.api_key = os.environ.get("ANTHROPIC_API_KEY", config.api_key)
        config.model = os.environ.get("ANTHROPIC_MODEL", config.model)
        if max_tokens := os.environ.get("ANTHROPIC_MAX_TOKENS"):
            config.max_tokens = int(max_tokens)
        if max_turns := os.environ.get("AGENT_MAX_TURNS"):
            config.max_turns = int(max_turns)
        if mode := os.environ.get("AGENT_PERMISSION_MODE"):
            config.permission_mode = mode
        return config

    @classmethod
    def from_file(cls, path: str | Path) -> AgentConfig:
        """Load configuration from a JSON file."""
        path = Path(path)
        if not path.exists():
            return cls.from_env()

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        config = cls.from_env()
        for key, value in data.items():
            if hasattr(config, key):
                setattr(config, key, value)
        return config

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "max_turns": self.max_turns,
            "system_prompt": self.system_prompt,
            "python_timeout": self.python_timeout,
            "python_memory_mb": self.python_memory_mb,
            "permission_mode": self.permission_mode,
            "enable_compression": self.enable_compression,
        }
