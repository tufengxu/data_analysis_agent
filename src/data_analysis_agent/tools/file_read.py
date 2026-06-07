"""FileReadTool: read local files with offset/limit pagination.

Security: read-only, concurrency-safe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class FileReadTool(Tool):
    """Read the contents of a file."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file at the given path. "
            "Optionally specify offset (line number) and limit (max lines)."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (0-indexed, optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (optional)",
                },
            },
            "required": ["file_path"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        file_path = input_data.get("file_path")
        if not file_path or not isinstance(file_path, str):
            return ValidationResult.fail("file_path is required and must be a string")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        file_path = Path(input_data["file_path"]).expanduser().resolve()
        offset = input_data.get("offset", 0)
        limit = input_data.get("limit")

        if not file_path.exists():
            return ToolResult(content=f"Error: File not found: {file_path}", is_error=True)
        if not file_path.is_file():
            return ToolResult(content=f"Error: Not a file: {file_path}", is_error=True)

        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        total_lines = len(lines)
        start = max(0, offset)
        end = total_lines if limit is None else min(total_lines, start + limit)
        selected = lines[start:end]

        content = "".join(selected)
        header = f"--- {file_path} (lines {start}-{end} of {total_lines}) ---\n"
        return ToolResult(content=header + content)
