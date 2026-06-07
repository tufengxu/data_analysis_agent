"""Message and ContentBlock type system for Anthropic API protocol.

Maps to Claude Code's ContentBlock hierarchy:
- TextBlock / ToolUseBlock / ToolResultBlock / ThinkingBlock
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class BlockType(Enum):
    TEXT = auto()
    TOOL_USE = auto()
    TOOL_RESULT = auto()
    THINKING = auto()
    REDACTED_THINKING = auto()


@dataclass(frozen=True)
class ContentBlock:
    """Base for all content blocks in Anthropic Messages API."""

    def to_api_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def from_api_dict(d: dict[str, Any]) -> ContentBlock:
        t = d.get("type")
        if t == "text":
            return TextBlock(text=d.get("text", ""))
        if t == "tool_use":
            return ToolUseBlock(
                id=d["id"],
                name=d["name"],
                input=d.get("input", {}),
            )
        if t == "tool_result":
            return ToolResultBlock(
                tool_use_id=d["tool_use_id"],
                content=d.get("content", ""),
                is_error=d.get("is_error", False),
            )
        if t == "thinking":
            return ThinkingBlock(
                thinking=d.get("thinking", ""),
                signature=d.get("signature"),
            )
        raise ValueError(f"Unknown content block type: {t}")


@dataclass(frozen=True)
class TextBlock(ContentBlock):
    text: str = ""

    def to_api_dict(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass(frozen=True)
class ToolUseBlock(ContentBlock):
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass(frozen=True)
class ToolResultBlock(ContentBlock):
    tool_use_id: str = ""
    content: str = ""
    is_error: bool = False

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }


@dataclass(frozen=True)
class ThinkingBlock(ContentBlock):
    thinking: str = ""
    signature: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": "thinking", "thinking": self.thinking}
        if self.signature:
            result["signature"] = self.signature
        return result


@dataclass
class ModelResponse:
    """Parsed response from Anthropic API."""

    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str | None = None  # "end_turn" | "tool_use" | "max_tokens" | ...
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)

    def has_tool_use(self) -> bool:
        return any(isinstance(b, ToolUseBlock) for b in self.content)

    def get_text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def get_tool_use_blocks(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]
