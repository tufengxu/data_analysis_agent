"""Configuration management for the data analysis agent.

Loads from:
1. Default values
2. Config file (JSON/YAML)
3. Environment variables
4. CLI arguments
"""

from __future__ import annotations

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
        "query data sources, and generate visualizations. Classify each request before acting: "
        "answer simple questions directly, use one tool directly for simple single-tool tasks, "
        "and write a concise plan before executing complex multi-step tasks. "
        "When a matching skill is active, follow that skill before generic reasoning or tools."
    )

    # Tool settings
    python_timeout: int = 30
    python_memory_mb: int = 512
    max_result_chars: int = 50_000

    # Permission settings
    permission_mode: str = "default"  # default | plan | auto | bypass
    deny_patterns: list[str] = field(default_factory=list)

    # Context management
    context_budget_tokens: int = 180_000
    enable_compression: bool = True

    # Result sampling / compaction
    sampling_trigger_chars: int = 8000
    sampling_fidelity: str = "mid"  # low | mid | high

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
