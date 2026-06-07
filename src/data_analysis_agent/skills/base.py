"""Base Skill interface and types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillResult:
    """Result of skill execution."""

    output: str = ""
    tools_used: list[str] = field(default_factory=list)
    execution_time_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class Skill(ABC):
    """Abstract base class for all skills."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique skill name."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for routing."""

    @property
    @abstractmethod
    def instructions(self) -> str:
        """Detailed instructions injected into the prompt when this skill is active."""

    @property
    def keywords(self) -> list[str]:
        """Routing keywords and phrases beyond the name and description."""
        return []

    @property
    def allowed_tools(self) -> list[str]:
        """Whitelist of tool names this skill may use."""
        return []

    @property
    def input_schema(self) -> dict[str, Any] | None:
        """Optional JSON schema for skill parameters."""
        return None

    @abstractmethod
    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        """Execute the skill with the given query and context."""

    def to_skill_tool_schema(self) -> dict[str, Any]:
        """Schema for the meta SkillTool that routes to this skill."""
        return {
            "name": self.name,
            "description": self.description,
        }
