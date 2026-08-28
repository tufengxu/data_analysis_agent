"""Query pushdown over cached tool results (D6): single-predicate slicing.

Pure stdlib, capability-layer home: the v1 ``retrieve_result`` tool and the
serving capability share this one implementation, so every harness gets the
same structured recall. Deliberately NOT a SQL engine — one predicate, one
projection, three row modes; exact aggregates and complex questions belong
in the kernel (python_analysis), per the retrieve_result tool guidance.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from .result_store import _MAX_PAGE_CHARS
from .text_summary import detect_table

_MODES = ("page", "head", "tail", "sample")
_FILTER_RE = re.compile(r"^\s*(?P<col>[^<>=!]+?)\s*(?P<op>>=|<=|==|!=|>|<)\s*(?P<value>.+?)\s*$")


class SliceError(ValueError):
    """Fail-closed slicing error with a model-actionable message."""


@dataclass
class TableSlice:
    result_id: str
    tool: str
    mode: str
    headers: list[str]
    rows: list[list[str]]
    matched: int
    total: int


def parse_filter(filter_text: str) -> tuple[str, str, str]:
    """Parse ``"col op value"`` (op ∈ >,>=,<,<=,==,!=); raise SliceError."""
    match = _FILTER_RE.match(filter_text)
    if match is None:
        raise SliceError("filter 必须形如 'col op value'(op ∈ >,>=,<,<=,==,!=),例如 'units>100'")
    return match.group("col").strip(), match.group("op"), match.group("value").strip()


def _compare(cell: str, op: str, value: str) -> bool:
    try:
        left: float | str = float(cell)
        right: float | str = float(value)
    except ValueError:
        left, right = cell.strip().lower(), value.lower()
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == ">":
        return left > right  # type: ignore[operator]
    if op == ">=":
        return left >= right  # type: ignore[operator]
    if op == "<":
        return left < right  # type: ignore[operator]
    return left <= right  # type: ignore[operator]


def _cell(row: list[str], headers: list[str], col: str) -> str:
    return row[headers.index(col)] if col in headers and headers.index(col) < len(row) else ""


def slice_stored_table(
    content: str,
    *,
    result_id: str,
    tool: str = "",
    mode: str = "head",
    columns: list[str] | None = None,
    filter_text: str | None = None,
    limit: int = 50,
    seed: int = 0,
) -> TableSlice:
    """Slice cached table content: filter → mode-select → (render-time) project.

    Raises :class:`SliceError` on unparseable content, unknown filter/projection
    columns, or an unknown mode — callers turn that into an error result.
    """
    if mode not in _MODES or mode == "page":
        raise SliceError(
            f"mode 必须是 {'/'.join(m for m in _MODES if m != 'page')}(分页用默认 page)"
        )
    parsed = detect_table(content)
    if parsed is None:
        raise SliceError("缓存内容不是可解析的表格(markdown/CSV/空白对齐);请用默认分页模式")
    headers, rows = parsed

    if columns:
        missing = [c for c in columns if c not in headers]
        if missing:
            raise SliceError(f"未知列: {', '.join(missing)};可用列: {', '.join(headers)}")
    matched_rows = rows
    if filter_text:
        col, op, value = parse_filter(filter_text)
        if col not in headers:
            raise SliceError(f"过滤列不存在: {col};可用列: {', '.join(headers)}")
        matched_rows = [row for row in rows if _compare(_cell(row, headers, col), op, value)]

    if mode == "head":
        selected = matched_rows[: max(1, limit)]
    elif mode == "tail":
        selected = matched_rows[-max(1, limit) :]
    else:  # sample
        rng = random.Random(seed)
        k = min(max(1, limit), len(matched_rows))
        selected = rng.sample(matched_rows, k)
        selected.sort(key=matched_rows.index)

    projected_headers = columns if columns else headers
    if columns:
        indexes = [headers.index(c) for c in columns]
        selected = [[row[i] if i < len(row) else "" for i in indexes] for row in selected]

    return TableSlice(
        result_id=result_id,
        tool=tool,
        mode=mode,
        headers=projected_headers,
        rows=selected,
        matched=len(matched_rows),
        total=len(rows),
    )


def render_slice(table: TableSlice) -> str:
    """Compact pipe-table render with the recall-page header convention."""
    header = (
        f"[result_id={table.result_id} | mode={table.mode} "
        f"{len(table.rows)} of {table.matched} matched / {table.total} rows"
        f" | tool={table.tool}]"
    )
    lines = [
        header,
        "| " + " | ".join(table.headers) + " |",
        "|" + "|".join("---" for _ in table.headers) + "|",
    ]
    for row in table.rows:
        cells = [str(c).replace("\n", " ").replace("|", "\\|") for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    text = "\n".join(lines)
    if len(text) > _MAX_PAGE_CHARS:
        text = text[:_MAX_PAGE_CHARS] + "\n…[页过大已截断,缩小 limit 或加 filter]"
    return text
