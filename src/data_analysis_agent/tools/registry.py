"""ToolRegistry: three-stage registration (enumerate → filter → assemble).

Mirrors Claude Code's multi-stage registry pattern:
1. get_all_base_tools() - hard-coded core tools
2. get_tools(mode) - runtime filtering by deny rules + permission mode
3. assemble_tool_pool() - merge + deduplicate + sort for cache stability
"""

from __future__ import annotations

from .base import Tool


class ToolRegistry:
    """Registry managing tool discovery, filtering, and assembly."""

    def __init__(self) -> None:
        self._base_tools: list[Tool] = []
        self._deny_patterns: set[str] = set()

    def register(self, tool: Tool) -> None:
        """Register a tool (first stage)."""
        self._base_tools.append(tool)

    def unregister(self, name: str) -> None:
        """Remove a tool by name."""
        self._base_tools = [t for t in self._base_tools if t.name != name]

    def add_deny_pattern(self, pattern: str) -> None:
        """Add a glob-style deny pattern."""
        self._deny_patterns.add(pattern)

    def get_all_base_tools(self) -> list[Tool]:
        """Stage 1: enumerate candidate set."""
        return list(self._base_tools)

    def get_tools(self, mode: str = "default") -> list[Tool]:
        """Stage 2: runtime filtering.

        Args:
            mode: "default" | "plan" (plan mode keeps only read-only tools)
        """
        tools = self._base_tools

        # Remove denied tools
        tools = [t for t in tools if not self._is_denied(t.name)]

        # Plan mode: keep only read-only tools
        if mode == "plan":
            tools = [t for t in tools if t.is_read_only({})]

        return tools

    def assemble_tool_pool(self, mode: str = "default") -> list[Tool]:
        """Stage 3: assemble final tool pool for the model.

        Returns tools sorted by name for prompt cache stability.
        """
        tools = self.get_tools(mode)
        return sorted(tools, key=lambda t: t.name)

    def get_tool(self, name: str) -> Tool | None:
        """Lookup a tool by exact name."""
        for t in self._base_tools:
            if t.name == name:
                return t
        return None

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return [t.name for t in self._base_tools]

    def _is_denied(self, tool_name: str) -> bool:
        """Check if a tool name matches any deny pattern."""
        import fnmatch

        return any(fnmatch.fnmatch(tool_name, p) for p in self._deny_patterns)
