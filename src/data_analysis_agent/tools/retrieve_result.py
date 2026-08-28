"""RetrieveResultTool: page through — or structurally slice — a cached result."""

from __future__ import annotations

import re
from typing import Any

from ..sampling.result_store import ResultStore
from ..sampling.slicing import render_slice, slice_stored_table
from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

_FILTER_SHAPE_RE = re.compile(r"^\s*[^<>=!]+?\s*(>=|<=|==|!=|>|<)\s*.+$")
_MODES = ("page", "head", "tail", "sample")


class RetrieveResultTool(Tool):
    """Return the original of a previously summarized tool result.

    Default mode pages through raw lines; mode=head/tail/sample plus
    columns/filter perform query pushdown over the cached table (single
    predicate — complex questions belong in python_analysis).
    """

    def __init__(self, result_store: ResultStore | None = None) -> None:
        self.result_store = result_store

    @property
    def name(self) -> str:
        return "retrieve_result"

    @property
    def description(self) -> str:
        return (
            "Retrieve the full original content of a previously summarized tool result. "
            "Large tool results are summarized in context and tagged with a result_id; "
            "page through the original by line via offset/limit, optionally filtering with "
            "a case-insensitive query substring. Structured recall: mode=head|tail|sample "
            "plus columns=[...] projection and a single filter like 'units>100' slice the "
            "cached table directly. For exact aggregates (sum/count/ratio), "
            "recompute in pandas via python_analysis instead of reading raw rows."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "result_id": {
                    "type": "string",
                    "description": "id from the '[完整结果已缓存...]' retrieval marker",
                },
                "offset": {"type": "integer", "description": "0-based starting line (default 0)"},
                "limit": {
                    "type": "integer",
                    "description": "max lines to return, 1-500 (default 50)",
                },
                "query": {
                    "type": "string",
                    "description": "optional case-insensitive substring filter",
                },
                "mode": {
                    "type": "string",
                    "enum": list(_MODES),
                    "description": "page (default, raw line paging) or head/tail/sample table slicing",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "column projection for head/tail/sample modes",
                },
                "filter": {
                    "type": "string",
                    "description": "single table predicate 'col op value' (op: > >= < <= == !=)",
                },
            },
            "required": ["result_id"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        if not input_data.get("result_id"):
            return ValidationResult.fail("result_id is required")
        offset = input_data.get("offset", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            return ValidationResult.fail("offset must be a non-negative integer")
        limit = input_data.get("limit", 50)
        if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
            return ValidationResult.fail("limit must be an integer in 1..500")
        mode = input_data.get("mode", "page")
        if mode not in _MODES:
            return ValidationResult.fail(f"mode must be one of {'/'.join(_MODES)}")
        columns = input_data.get("columns")
        if columns is not None and (
            not isinstance(columns, list) or not all(isinstance(c, str) and c for c in columns)
        ):
            return ValidationResult.fail("columns must be a list of column names")
        filter_text = input_data.get("filter")
        if filter_text is not None and (
            not isinstance(filter_text, str) or not _FILTER_SHAPE_RE.match(filter_text)
        ):
            return ValidationResult.fail(
                "filter must look like 'col op value' (op: > >= < <= == !=)"
            )
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        if self.result_store is None:
            return ToolResult(
                content="Result retrieval is not available in this session.",
                is_error=True,
            )
        mode = input_data.get("mode", "page")
        if mode != "page":
            content = self.result_store.fetch_content(str(input_data["result_id"]))
            if content is None:
                return ToolResult(
                    content=(
                        f"result_id '{input_data['result_id']}' not found or expired (TTL=1h). "
                        "Recompute with python_analysis if needed."
                    ),
                    is_error=True,
                )
            columns = input_data.get("columns")
            try:
                table = slice_stored_table(
                    content,
                    result_id=str(input_data["result_id"]),
                    tool="",
                    mode=mode,
                    columns=columns if isinstance(columns, list) else None,
                    filter_text=input_data.get("filter"),
                    limit=int(input_data.get("limit", 50)),
                )
            except ValueError as exc:
                return ToolResult(content=f"Slice failed: {exc}", is_error=True)
            return ToolResult(content=render_slice(table))
        page = self.result_store.get(
            str(input_data["result_id"]),
            offset=int(input_data.get("offset", 0)),
            limit=int(input_data.get("limit", 50)),
            query=input_data.get("query"),
        )
        if page is None:
            return ToolResult(
                content=(
                    f"result_id '{input_data['result_id']}' not found or expired (TTL=1h). "
                    "Recompute with python_analysis if needed."
                ),
                is_error=True,
            )
        return ToolResult(content=page.text)
