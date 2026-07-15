"""FileReadTool: read local files with offset/limit pagination.

Security: read-only, concurrency-safe. Path-scoped to ``allowed_paths`` (same
fail-closed policy as data_profile / python_analysis): a path that does not
resolve under an allowed directory is rejected, so the model cannot wander the
filesystem via this tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

# Hard ceiling so a pathological file (no newline for megabytes) cannot pin
# memory; the agent paginates with offset/limit for anything larger.
MAX_READ_LINES = 20_000


class FileReadTool(Tool):
    """Read the contents of a file."""

    def __init__(self, allowed_paths: list[str | Path] | None = None) -> None:
        self.allowed_paths = [
            Path(p).expanduser().resolve() for p in (allowed_paths or [Path.cwd()])
        ]

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

    def _within_allowed(self, resolved: Path) -> bool:
        for allowed in self.allowed_paths:
            if resolved == allowed or resolved.is_relative_to(allowed):
                return True
        return False

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        # resolve() follows symlinks to the real target before the whitelist
        # check, so a symlink planted inside an allowed dir but pointing outside
        # is rejected by its resolved location.
        file_path = Path(input_data["file_path"]).expanduser().resolve()
        offset = input_data.get("offset", 0)
        limit = input_data.get("limit")

        if not self._within_allowed(file_path):
            return ToolResult(
                content=f"Error: path is outside allowed analysis paths: {input_data['file_path']}",
                is_error=True,
            )
        if not file_path.exists():
            return ToolResult(content=f"Error: File not found: {file_path}", is_error=True)
        if not file_path.is_file():
            return ToolResult(content=f"Error: Not a file: {file_path}", is_error=True)

        start = max(0, offset)
        cap = MAX_READ_LINES if limit is None else min(limit, MAX_READ_LINES)
        selected: list[str] = []
        # cap <= 0 (e.g. limit=0/negative) -> read nothing; matches the old
        # lines[start:start+0] semantics instead of the streaming loop's
        # append-then-break, which would have returned one extra line.
        if cap > 0:
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f):
                        if i < start:
                            continue
                        selected.append(line)
                        if len(selected) >= cap:
                            break
            except Exception as e:
                return ToolResult(content=f"Error reading file: {e}", is_error=True)

        end = start + len(selected)
        # "capped" is only meaningful when we actually hit the hard ceiling for a
        # boundless request; a file of exactly MAX_READ_LINES lines with no limit
        # is fully read, but we can't tell that from a bounded read without an
        # extra probe, so this flag is intentionally conservative (may over-report).
        capped = len(selected) >= MAX_READ_LINES and (limit is None or limit > MAX_READ_LINES)
        header = f"--- {file_path} (lines {start}-{end}{', capped at MAX_READ_LINES' if capped else ''}) ---\n"
        return ToolResult(content=header + "".join(selected))
