"""DataProfileTool: read-only structural discovery of data files and directories.

Gives the model a deterministic map of WHAT data exists before it writes any
analysis code — the missing affordance for multi-sheet and multi-file work:

- a CSV / TSV / Parquet file -> its columns, dtypes and sampled row count
- an Excel workbook          -> one profile per sheet, so sheets are discoverable
                                rather than blind-probed
- a directory                -> the tabular files it holds, each with a column
                                preview, so the model can spot shared join keys

Design constraints (mirror the existing data-read policy):
- Read-only and deterministic; reads files in-process like ``memory.profiler``
  and never executes model-supplied code.
- Path-scoped to ``allowed_paths`` (same fail-closed policy as python_analysis).
- Emits ABSOLUTE paths so the model can copy them straight into read_csv()/
  read_excel(); a relative path would resolve against the kernel's temp cwd,
  not the data directory.
- pandas is optional: CSV/TSV degrade to a stdlib reader (columns + row count,
  dtype "unknown"); Excel/Parquet require pandas and report a clear error if
  it is missing.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

_SAMPLE_ROWS = 1000
_MAX_DIR_FILES = 50
_MAX_PREVIEW_COLS = 12
# Rows scanned (header=None) to detect a shifted Excel header (title/blank rows
# above the real header). 8 covers the common "report title + blank + sub-title
# + blank + header" layout without reading much extra.
_HEADER_SCAN_ROWS = 8

_DELIMITERS = {".csv": ",", ".tsv": "\t"}
_EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
_PARQUET_SUFFIXES = {".parquet"}
_SUPPORTED = set(_DELIMITERS) | _EXCEL_SUFFIXES | _PARQUET_SUFFIXES


def _format_for(suffix: str) -> str:
    if suffix in _EXCEL_SUFFIXES:
        return "excel"
    if suffix in _PARQUET_SUFFIXES:
        return "parquet"
    return suffix.lstrip(".") or "unknown"


def _table(
    sheet: str | None,
    columns: list[dict[str, str]],
    n_rows: int,
    sampled: bool,
    header_offset: int = 0,
) -> dict[str, Any]:
    return {
        "sheet": sheet,
        "n_cols": len(columns),
        "columns": columns,
        "n_rows_sampled": n_rows,
        "sampled": sampled,
        "header_offset": header_offset,
    }


def _count_nonnull(row: Any) -> int:
    """Non-null cells in a header=None row (a pandas Series)."""
    return int(row.notna().sum())


def _row_is_all_string(row: Any) -> bool:
    """True if every non-null cell in the row is a string (header-name-like).

    A real header row is all column-name strings; a data row carrying any
    numeric/temporal value is not. Used to reject data rows that a blank-row
    layout signal alone might otherwise promote to the header.
    """
    non_null = row.dropna()
    if int(len(non_null)) == 0:
        return False
    return all(isinstance(v, str) for v in non_null.tolist())


def _read_delimited_stdlib(path: Path, sep: str) -> tuple[list[str], int, bool]:
    """Columns + sampled row count using only the stdlib csv reader.

    Reads one row past the cap so a file with exactly ``_SAMPLE_ROWS`` rows is
    reported as fully read (``sampled=False``), not as truncated. Unlike the
    pandas path this does NOT de-duplicate repeated header names (pandas would
    rename ``a, a`` to ``a, a.1``); the profile is advisory, so the raw header
    is reported as-is.
    """
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter=sep)
        header = next(reader, None)
        if header is None:
            return [], 0, False
        columns = [c.strip() for c in header]
        count = 0
        for count, _row in enumerate(reader, start=1):  # noqa: B007
            if count > _SAMPLE_ROWS:
                break
        sampled = count > _SAMPLE_ROWS
        return columns, min(count, _SAMPLE_ROWS), sampled


def _profile_delimited(path: Path, sep: str) -> dict[str, Any]:
    """One table profile for a delimited file (pandas for dtypes, stdlib fallback)."""
    try:
        import pandas as pd
    except ImportError:
        cols, n_rows, sampled = _read_delimited_stdlib(path, sep)
        columns = [{"name": c, "dtype": "unknown"} for c in cols]
        return _table(None, columns, n_rows, sampled)

    # nrows = cap + 1 to tell "exactly cap rows" apart from "more than cap".
    try:
        df = pd.read_csv(path, sep=sep, nrows=_SAMPLE_ROWS + 1)
    except pd.errors.EmptyDataError:
        # No header/rows: match the stdlib path's benign empty profile.
        return _table(None, [], 0, False)
    total = int(len(df))
    columns = [{"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns]
    return _table(None, columns, min(total, _SAMPLE_ROWS), total > _SAMPLE_ROWS)


def _profile_parquet(path: Path) -> dict[str, Any]:
    # Prefer parquet metadata: exact row count + schema without loading the file.
    try:
        import pyarrow.parquet as pq

        schema = pq.read_schema(path)
        n_rows = int(pq.read_metadata(path).num_rows)
        columns = [
            {"name": str(name), "dtype": str(schema.field(name).type)} for name in schema.names
        ]
        return _table(None, columns, n_rows, False)
    except ImportError:
        import pandas as pd

        df = pd.read_parquet(path)
        columns = [{"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns]
        return _table(None, columns, int(len(df)), False)


def _detect_header_offset(raw_grid: Any) -> int:
    """Detect the real header row index when title/blank rows sit above it.

    ``raw_grid`` is a DataFrame read with ``header=None``. Three gates (ALL must
    pass) keep this zero-false-positive on realistic clean sheets:

    1. Density: a lower row is denser than row 0 by ≥2 cells (``best >= row0+2``)
       — so a clean header with one empty cell, or ragged data, does not fire.
    2. A fully-BLANK row sits above the candidate (in ``range(0, cand)``). A
       blank row is an unambiguous layout artifact (title / spacer rows above the
       real header); clean data tables never contain a fully-blank row.
    3. The candidate row is all-string (column names). A data row carrying any
       numeric/temporal value is rejected even when a blank spacer row sits above
       it.

    Returns 0 (no shift) whenever any gate fails — conservative by design: a
    title directly on the header (no blank row) is missed, and an irreducibly
    ambiguous degenerate case (a header with ≥2 unnamed cells + a blank spacer
    row + all-text data) may still fire — not a realistic clean sheet.
    """
    n_rows = int(len(raw_grid))
    if n_rows == 0:
        return 0
    n_scan = min(n_rows, _HEADER_SCAN_ROWS)
    counts = [_count_nonnull(raw_grid.iloc[i]) for i in range(n_scan)]
    row0 = counts[0]
    best = max(counts)
    if best >= row0 + 2:
        cand = counts.index(best)
        # Require (a) a fully-blank row above the candidate (layout artifact —
        # clean data tables have none) AND (b) the candidate row is all-string
        # (header names, not data values). Together these make a data row with a
        # numeric value — even when a blank spacer row sits above it — rejected.
        if any(counts[i] == 0 for i in range(0, cand)) and _row_is_all_string(raw_grid.iloc[cand]):
            return cand
    return 0


def _profile_excel(path: Path) -> list[dict[str, Any]]:
    import pandas as pd

    workbook = pd.ExcelFile(path)
    tables: list[dict[str, Any]] = []
    for sheet in workbook.sheet_names:
        # First pass: detect a shifted header (title/blank rows above the real one).
        offset = 0
        try:
            raw = workbook.parse(sheet, header=None, nrows=_HEADER_SCAN_ROWS)
            offset = _detect_header_offset(raw)
        except Exception:  # pragma: no cover - defensive; fall back to header=0
            offset = 0
        # Second pass: parse with the detected header so columns/rows are real.
        try:
            frame = workbook.parse(sheet, header=offset, nrows=_SAMPLE_ROWS + 1)
        except Exception:
            frame = workbook.parse(sheet, nrows=_SAMPLE_ROWS + 1)
            offset = 0
        columns = [{"name": str(c), "dtype": str(frame[c].dtype)} for c in frame.columns]
        total = int(len(frame))
        tables.append(
            _table(str(sheet), columns, min(total, _SAMPLE_ROWS), total > _SAMPLE_ROWS, offset)
        )
    return tables


def _profile_file(path: Path) -> dict[str, Any]:
    """Structured profile for a single supported data file. Raises on read errors."""
    suffix = path.suffix.lower()
    if suffix in _DELIMITERS:
        tables = [_profile_delimited(path, _DELIMITERS[suffix])]
    elif suffix in _PARQUET_SUFFIXES:
        tables = [_profile_parquet(path)]
    elif suffix in _EXCEL_SUFFIXES:
        tables = _profile_excel(path)
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"unsupported file type '{suffix}'")
    return {
        "kind": "file",
        "path": str(path),
        "format": _format_for(suffix),
        "tables": tables,
    }


def _profile_dir(path: Path, within_allowed: Callable[[Path], bool]) -> dict[str, Any]:
    """List the tabular files in a directory with a lightweight per-file preview."""
    candidates = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in _SUPPORTED)
    # call() only validated the directory itself; a symlink inside it can still
    # resolve OUTSIDE allowed_paths, so re-check every child against the whitelist.
    in_scope = [p for p in candidates if within_allowed(p.resolve())]
    files: list[dict[str, Any]] = []
    truncated = len(in_scope) > _MAX_DIR_FILES
    for child in in_scope[:_MAX_DIR_FILES]:
        entry: dict[str, Any] = {"name": child.name, "format": _format_for(child.suffix.lower())}
        try:
            profile = _profile_file(child)
        except Exception as exc:  # one bad file must not sink the whole listing
            entry["error"] = str(exc)
            files.append(entry)
            continue
        tables = profile["tables"]
        if profile["format"] == "excel":
            entry["sheets"] = [t["sheet"] for t in tables]
        elif tables:
            entry["columns_preview"] = [c["name"] for c in tables[0]["columns"]]
        files.append(entry)
    return {
        "kind": "directory",
        "path": str(path),
        "files": files,
        "truncated": truncated,
        "n_total": len(in_scope),
    }


def _render_file(profile: dict[str, Any]) -> str:
    lines = [f"File: {profile['path']}  [{profile['format']}]"]
    tables = profile["tables"]
    is_excel = profile["format"] == "excel"
    if is_excel:
        lines[0] = f"File: {profile['path']}  [excel, {len(tables)} sheets]"
    for table in tables:
        sampled = " (sampled)" if table["sampled"] else ""
        head = (
            f'sheet "{table["sheet"]}": ' if is_excel else ""
        ) + f"{table['n_rows_sampled']} rows{sampled} x {table['n_cols']} cols"
        lines.append(head)
        if is_excel and table.get("header_offset"):
            lines.append(
                f"    ⚠ real header at row {table['header_offset']} "
                f"(title/blank rows above) — re-read with "
                f"pd.read_excel(..., header={table['header_offset']}) "
                f"or skiprows={table['header_offset']}"
            )
        for col in table["columns"]:
            lines.append(f"  - {col['name']} ({col['dtype']})")
    return "\n".join(lines)


def _render_dir(profile: dict[str, Any]) -> str:
    files = profile["files"]
    n_total = profile.get("n_total", len(files))
    if profile["truncated"]:
        header = (
            f"Directory: {profile['path']}  ({n_total} tabular files, showing first {len(files)})"
        )
    else:
        header = f"Directory: {profile['path']}  ({len(files)} tabular files)"
    lines = [header]
    for entry in files:
        if "error" in entry:
            lines.append(f"  - {entry['name']} [{entry['format']}]: <unreadable: {entry['error']}>")
        elif "sheets" in entry:
            lines.append(
                f"  - {entry['name']} [{entry['format']}, sheets: {', '.join(entry['sheets'])}]"
            )
        else:
            cols = entry.get("columns_preview", [])
            preview = ", ".join(cols[:_MAX_PREVIEW_COLS])
            if len(cols) > _MAX_PREVIEW_COLS:
                preview += f", …(+{len(cols) - _MAX_PREVIEW_COLS} more)"
            lines.append(f"  - {entry['name']} [{entry['format']}]: {preview}")
    if profile["truncated"]:
        lines.append(f"  …(listing truncated to first {_MAX_DIR_FILES} files)")
    return "\n".join(lines)


class DataProfileTool(Tool):
    """Profile a data file (incl. every Excel sheet) or list a directory's datasets."""

    def __init__(self, allowed_paths: list[str | Path] | None = None) -> None:
        self.allowed_paths = [
            Path(p).expanduser().resolve() for p in (allowed_paths or [Path.cwd()])
        ]

    @property
    def name(self) -> str:
        return "data_profile"

    @property
    def description(self) -> str:
        return (
            "Inspect the STRUCTURE of local data before analysing it. "
            "Pass a file path to get its columns, dtypes and sampled row count "
            "(every sheet is profiled for Excel .xlsx/.xls workbooks), or pass a "
            "directory path to list the tabular files it contains with a column "
            "preview. Use this first to discover sheets and to find shared join "
            "keys across files, then copy the ABSOLUTE paths it reports into "
            "python_analysis (pd.read_csv / pd.read_excel)."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to a data file (.csv/.tsv/.parquet/.xlsx/.xls) or a directory",
                },
            },
            "required": ["path"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        path = input_data.get("path")
        if not path or not isinstance(path, str):
            return ValidationResult.fail("path is required and must be a string")
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
        target = Path(input_data["path"]).expanduser().resolve()

        if not self._within_allowed(target):
            return ToolResult(
                content=f"Error: path is outside allowed analysis paths: {input_data['path']}",
                is_error=True,
            )
        if not target.exists():
            return ToolResult(content=f"Error: path not found: {target}", is_error=True)

        if target.is_dir():
            profile = _profile_dir(target, self._within_allowed)
            return ToolResult(content=_render_dir(profile), metadata={"profile": profile})

        suffix = target.suffix.lower()
        if suffix not in _SUPPORTED:
            supported = ", ".join(sorted(_SUPPORTED))
            return ToolResult(
                content=f"Error: unsupported file type '{suffix}' (supported: {supported})",
                is_error=True,
            )
        try:
            profile = _profile_file(target)
        except ImportError:
            return ToolResult(
                content=(
                    f"Error: reading {suffix} requires pandas (+openpyxl for Excel). "
                    "Install the 'data' extra."
                ),
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(content=f"Error profiling {target}: {exc}", is_error=True)

        return ToolResult(content=_render_file(profile), metadata={"profile": profile})
