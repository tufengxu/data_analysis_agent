"""DataQualityTool: read-only data-quality checks on a single tabular file.

The complement to ``DataProfileTool``. ``data_profile`` discovers STRUCTURE
(columns / dtypes / row count / sheets / directory listing); ``data_quality``
inspects a single table for the things that make analysis wrong before the
model trusts it:

- missingness (per column, %), empty strings in text columns
- duplicate rows (table-level)
- per-column uniqueness, all-unique / identifier-like / duplicate-key-risk
- constant columns
- numeric outliers (IQR), zeros, negatives, min/max/mean/median
- type anomalies: numbers or dates stored as text
- high-cardinality and high-missing signals

Design constraints (mirror the existing data-read policy):
- Read-only and deterministic; like ``data_profile`` it never executes
  model-supplied code.
- Path-scoped to ``allowed_paths`` (same fail-closed policy as python_analysis).
- Emits ABSOLUTE paths so the model can copy them straight into read_csv()/
  read_excel(); a relative path would resolve against the kernel's temp cwd.
- File-only: directory listing is ``data_profile``'s discovery job; "quality of
  a directory" is undefined, so we keep the two tools non-overlapping.
- pandas is a hard requirement. Structure discovery can degrade to a stdlib csv
  reader; quality statistics (missingness / outliers / cardinality) cannot, so
  we surface a clear error instead of returning meaningless numbers.
- Reads the FULL table (quality counts are only correct over full data, unlike
  ``data_profile``'s 1000-row structural sample), with a 1,000,000-row safety
  cap that is reported honestly via a ``truncated`` flag when hit.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

_MAX_ROWS = 1_000_000
# Type-anomaly heuristics (numeric_ratio / date_ratio) parse at most this many
# non-null values per object column — the signal is stable over a sample and
# parsing every cell of a wide text column is wasteful.
_TYPE_SAMPLE = 1000
_HIGH_MISSING_PCT = 50.0
_HIGH_OUTLIER_PCT = 5.0
_TYPE_RATIO_THRESHOLD = 0.9

_DELIMITERS = {".csv": ",", ".tsv": "\t"}
_EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
_PARQUET_SUFFIXES = {".parquet"}
_SUPPORTED = set(_DELIMITERS) | _EXCEL_SUFFIXES | _PARQUET_SUFFIXES

# Identifier-like column names. Matched at token granularity so "note" does NOT
# fire on the token "no", while "order_id", "customer_num", "uid" do.
_ID_TOKENS = frozenset({"id", "identifier", "code", "key", "no", "num", "index", "uid", "uuid"})
# CamelCase / PascalCase boundaries, applied before token-splitting so names
# like "CustomerNum" / "OrderId" yield ["customer","num"] / ["order","id"].
_CAMEL_LOWER_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_UPPER_LOWER = re.compile(r"([A-Z]+)([A-Z][a-z])")


def _format_for(suffix: str) -> str:
    if suffix in _EXCEL_SUFFIXES:
        return "excel"
    if suffix in _PARQUET_SUFFIXES:
        return "parquet"
    return suffix.lstrip(".") or "unknown"


def _is_id_like_name(name: str) -> bool:
    # Split on non-alphanumeric, then on camelCase boundaries, so the heuristic
    # works for snake_case, camelCase and PascalCase without substring false
    # positives ("note" must not match the "no" token).
    parts = re.split(r"[^0-9a-zA-Z]+", name)
    tokens: list[str] = []
    for part in parts:
        spaced = _CAMEL_UPPER_LOWER.sub(r"\1 \2", part)
        spaced = _CAMEL_LOWER_UPPER.sub(r"\1 \2", spaced)
        tokens.extend(spaced.split())
    return any(tok.lower() in _ID_TOKENS for tok in tokens)


def _try_float(value: object) -> bool:
    try:
        float(value)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


def _ratio_parseable(series_values: list[Any], parser: Callable[[object], bool]) -> float:
    """Fraction of the (already non-null) sample that ``parser`` accepts.

    Returns 0.0 for an empty sample rather than dividing by zero.
    """
    sample = series_values[:_TYPE_SAMPLE]
    if not sample:
        return 0.0
    return sum(1 for v in sample if parser(v)) / len(sample)


def _numeric_stats(series: Any, n_rows: int) -> dict[str, Any]:
    """Type-conditional stats for a numeric column (IQR outliers included)."""
    import numpy as np

    clean = series.dropna()
    n_valid = int(len(clean))
    as_float = clean.astype(float)

    q1 = q3 = 0.0
    n_out_low = n_out_high = 0
    if n_valid:
        arr = np.asarray(as_float.to_numpy(), dtype=float)
        q1 = float(np.percentile(arr, 25))
        q3 = float(np.percentile(arr, 75))
        fence = 1.5 * (q3 - q1)
        n_out_low = int((arr < q1 - fence).sum())
        n_out_high = int((arr > q3 + fence).sum())
    n_outliers = n_out_low + n_out_high
    outlier_pct = round(n_outliers / n_valid * 100, 2) if n_valid else 0.0
    return {
        "min": round(float(as_float.min()), 6) if n_valid else None,
        "max": round(float(as_float.max()), 6) if n_valid else None,
        "mean": round(float(as_float.mean()), 6) if n_valid else None,
        "median": round(float(as_float.median()), 6) if n_valid else None,
        "n_zeros": int((as_float == 0).sum()),
        "n_negative": int((as_float < 0).sum()),
        "n_outliers": n_outliers,
        "outlier_pct": outlier_pct,
    }


def _text_stats(series: Any) -> dict[str, Any]:
    """Type-conditional stats for an object column.

    ``numeric_ratio`` / ``date_ratio`` are computed over the first
    ``_TYPE_SAMPLE`` non-null string values — they are advisory signals, not
    exact counts.
    """
    clean = series.dropna().astype(str)
    n_empty = int(clean.str.strip().eq("").sum())
    non_empty = [v for v in clean.to_list() if v.strip() != ""]
    numeric_ratio = round(_ratio_parseable(non_empty, _try_float), 4)
    date_ratio = round(_ratio_parseable(non_empty, _is_date_like), 4)
    return {
        "n_empty_string": n_empty,
        "numeric_ratio": numeric_ratio,
        "date_ratio": date_ratio,
    }


_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")


def _is_date_like(value: object) -> bool:
    """Advisory date-shape check: ISO-ish YYYY-MM-DD[ HH:MM[:SS]]."""
    if not isinstance(value, str):
        return False
    return bool(_DATE_RE.match(value.strip()))


def _datetime_stats(series: Any) -> dict[str, Any]:
    """Type-conditional stats for a datetime column: ISO min/max only.

    Outlier/type-anomaly logic does not apply to a genuine datetime dtype, so we
    deliberately do NOT emit a ``text`` block (which would otherwise drive a
    spurious ``date_stored_as_text`` flag — the column is already datetime).
    """
    clean = series.dropna()
    if not len(clean):
        return {"min": None, "max": None}
    return {"min": str(clean.min()), "max": str(clean.max())}


def _column_quality(df: Any, col: str, n_rows: int) -> dict[str, Any]:
    """Build the per-column quality dict (base stats + type-conditional block + flags)."""
    import pandas as pd

    series = df[col]
    dtype_obj = series.dtype
    dtype = str(dtype_obj)
    # NaT counts as missing for datetimes; isna() already covers NaT/NaN/None.
    n_missing = int(series.isna().sum())
    missing_pct = round(n_missing / n_rows * 100, 2) if n_rows else 0.0
    n_unique = int(series.nunique(dropna=True))
    uniqueness = round(n_unique / n_rows, 6) if n_rows else 0.0
    # "constant" = exactly one distinct non-null value; an all-missing column
    # (n_unique == 0) is NOT constant, it is empty.
    is_constant = n_unique == 1

    # Route by dtype so type-anomaly flags only apply to object/string columns.
    # Complex numbers are excluded from the numeric path (their min/max would
    # silently drop the imaginary part); bool too — pandas treats bool as
    # numeric, but IQR outliers on a two-valued column are noise (an imbalanced
    # is_refunded/is_fraud column would otherwise get a false high_outliers).
    # datetime gets its own block so a real datetime column is never misflagged
    # as "date stored as text".
    is_numeric = (
        pd.api.types.is_numeric_dtype(dtype_obj)
        and not pd.api.types.is_complex_dtype(dtype_obj)
        and not pd.api.types.is_bool_dtype(dtype_obj)
    )
    is_datetime = pd.api.types.is_datetime64_any_dtype(dtype_obj)
    is_text = pd.api.types.is_object_dtype(dtype_obj) or pd.api.types.is_string_dtype(dtype_obj)

    entry: dict[str, Any] = {
        "name": str(col),
        "dtype": dtype,
        "n_missing": n_missing,
        "missing_pct": missing_pct,
        "n_unique": n_unique,
        "uniqueness": uniqueness,
        "is_constant": is_constant,
        "flags": [],
    }

    if is_numeric:
        entry["numeric"] = _numeric_stats(series, n_rows)
    elif is_datetime:
        entry["datetime"] = _datetime_stats(series)
    elif is_text:
        entry["text"] = _text_stats(series)
    # else (category / complex / bool / timedelta / period): base stats only.

    flags: list[str] = []
    if is_constant and n_rows > 0:
        flags.append("constant")
    if n_rows > 1 and n_unique == n_rows:
        flags.append("all_unique")
        if _is_id_like_name(str(col)):
            flags.append("identifier_like")
    if _is_id_like_name(str(col)) and n_rows > 1 and n_unique != n_rows:
        flags.append("duplicate_key_risk")
    if missing_pct >= _HIGH_MISSING_PCT:
        flags.append("high_missing")
    if is_numeric:
        if entry["numeric"]["outlier_pct"] >= _HIGH_OUTLIER_PCT:
            flags.append("high_outliers")
    elif is_text:
        text = entry["text"]
        if text["numeric_ratio"] >= _TYPE_RATIO_THRESHOLD:
            flags.append("numeric_stored_as_text")
        if text["date_ratio"] >= _TYPE_RATIO_THRESHOLD:
            flags.append("date_stored_as_text")
    entry["flags"] = flags
    return entry


def _table_quality(df: Any, sheet: str | None) -> dict[str, Any]:
    """Run all quality checks on one DataFrame; returns the structured table dict."""
    n_rows = int(len(df))
    n_cols = int(df.shape[1])
    n_duplicate_rows = int(df.duplicated().sum()) if n_rows else 0
    duplicate_row_pct = round(n_duplicate_rows / n_rows * 100, 2) if n_rows else 0.0
    columns = [_column_quality(df, col, n_rows) for col in df.columns]
    return {
        "sheet": sheet,
        "n_rows": n_rows,
        "n_cols": n_cols,
        "n_duplicate_rows": n_duplicate_rows,
        "duplicate_row_pct": duplicate_row_pct,
        "columns": columns,
    }


def _read_delimited(path: Path, sep: str) -> tuple[Any, bool]:
    """Read a delimited file fully (capped at _MAX_ROWS); return (df, truncated).

    An empty file (no header/rows) yields an empty DataFrame rather than an
    error, matching ``DataProfileTool``'s benign empty profile.
    """
    import pandas as pd

    try:
        df = pd.read_csv(path, sep=sep, nrows=_MAX_ROWS + 1)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), False
    truncated = int(len(df)) > _MAX_ROWS
    if truncated:
        df = df.iloc[:_MAX_ROWS]
    return df, truncated


def _read_parquet(path: Path) -> tuple[Any, bool]:
    import pandas as pd

    df = pd.read_parquet(path)
    return df, False  # columnar read; no row cap applied


def _read_excel(path: Path, sheet: str | None) -> tuple[list[Any], list[str], bool]:
    """Read Excel sheets; returns (frames, sheet_names, truncated).

    ``truncated`` is True if ANY requested sheet exceeds _MAX_ROWS.
    """
    import pandas as pd

    workbook = pd.ExcelFile(path)
    if sheet is not None:
        if sheet not in workbook.sheet_names:
            raise KeyError(sheet)
        targets = [sheet]
    else:
        targets = list(workbook.sheet_names)
    frames: list[Any] = []
    truncated = False
    for name in targets:
        frame = workbook.parse(name, nrows=_MAX_ROWS + 1)
        if int(len(frame)) > _MAX_ROWS:
            truncated = True
            frame = frame.iloc[:_MAX_ROWS]
        frames.append(frame)
    return frames, targets, truncated


def _check_file(path: Path, sheet: str | None) -> dict[str, Any]:
    """Read + quality-check a single file; raises on read errors."""
    suffix = path.suffix.lower()
    tables: list[dict[str, Any]] = []
    truncated = False
    if suffix in _DELIMITERS:
        df, truncated = _read_delimited(path, _DELIMITERS[suffix])
        tables.append(_table_quality(df, None))
    elif suffix in _PARQUET_SUFFIXES:
        df, truncated = _read_parquet(path)
        tables.append(_table_quality(df, None))
    elif suffix in _EXCEL_SUFFIXES:
        frames, names, truncated = _read_excel(path, sheet)
        for frame, name in zip(frames, names, strict=True):
            tables.append(_table_quality(frame, name))
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"unsupported file type '{suffix}'")
    return {
        "kind": "file",
        "path": str(path),
        "format": _format_for(suffix),
        "truncated": truncated,
        "tables": tables,
    }


def _render_table(table: dict[str, Any], is_excel: bool) -> list[str]:
    n_rows = table["n_rows"]
    n_cols = table["n_cols"]
    n_dup = table["n_duplicate_rows"]
    dup_pct = table["duplicate_row_pct"]
    head = f"{n_rows} rows x {n_cols} cols"
    if n_dup:
        head += f" · {n_dup} duplicate rows ({dup_pct}%)"
    lines = [f'sheet "{table["sheet"]}": {head}'] if is_excel else [head]
    for col in table["columns"]:
        flag_text = f" · {', '.join(col['flags'])}" if col["flags"] else ""
        lines.append(f"  - {col['name']} ({col['dtype']}){flag_text}")
        bits = [f"{col['n_missing']} missing ({col['missing_pct']}%)"]
        if not col["is_constant"]:
            bits.append(f"{col['n_unique']} unique")
        if "numeric" in col:
            num = col["numeric"]
            if num["n_outliers"]:
                bits.append(f"{num['n_outliers']} outliers ({num['outlier_pct']}%)")
            if num["min"] is not None:
                bits.append(f"min {num['min']}, max {num['max']}, mean {num['mean']}")
        if "datetime" in col:
            dt = col["datetime"]
            if dt["min"] is not None:
                bits.append(f"min {dt['min']}, max {dt['max']}")
        if "text" in col:
            txt = col["text"]
            if txt["n_empty_string"]:
                bits.append(f"{txt['n_empty_string']} empty strings")
            if txt["numeric_ratio"]:
                bits.append(f"~{txt['numeric_ratio']} numeric-like")
            if txt["date_ratio"]:
                bits.append(f"~{txt['date_ratio']} date-like")
        lines.append("      " + " · ".join(bits))
    return lines


def _render_file(report: dict[str, Any]) -> str:
    tables = report["tables"]
    is_excel = report["format"] == "excel"
    header = f"File: {report['path']}  [{report['format']}]"
    if is_excel:
        header += f", {len(tables)} sheet{'s' if len(tables) != 1 else ''}"
    lines = [header]
    if report["truncated"]:
        lines.append(
            f"  WARNING: file exceeded the {_MAX_ROWS} row cap; "
            "row-dependent metrics reflect the first "
            f"{_MAX_ROWS:,} rows only."
        )
    for table in tables:
        lines.extend(_render_table(table, is_excel))
    return "\n".join(lines)


class DataQualityTool(Tool):
    """Check the QUALITY of a single local data file before analysing it.

    Complements ``data_profile`` (structure) by reporting missingness, duplicate
    rows, per-column uniqueness, constant columns, numeric outliers, and type
    anomalies (numbers/dates stored as text). Pass a file path; for Excel you may
    pass ``sheet`` to check one sheet, otherwise every sheet is checked. Copy the
    ABSOLUTE path it reports into python_analysis when you decide to act on it.
    """

    def __init__(self, allowed_paths: list[str | Path] | None = None) -> None:
        self.allowed_paths = [
            Path(p).expanduser().resolve() for p in (allowed_paths or [Path.cwd()])
        ]

    @property
    def name(self) -> str:
        return "data_quality"

    @property
    def description(self) -> str:
        return (
            "Check the QUALITY of a local data file (.csv/.tsv/.parquet/.xlsx/.xls) "
            "before trusting it. Reports per-column missingness, duplicate rows, "
            "uniqueness, constant columns, numeric outliers (IQR), and type "
            "anomalies (numbers/dates stored as text). Use data_profile first to "
            "discover STRUCTURE, then data_quality to find what is dirty. For "
            "Excel, optionally pass a sheet name; otherwise every sheet is checked."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to a data file (.csv/.tsv/.parquet/.xlsx/.xls)",
                },
                "sheet": {
                    "type": "string",
                    "description": (
                        "Excel sheet name to check. If omitted, every sheet is "
                        "checked. Ignored for non-Excel formats."
                    ),
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

        suffix = target.suffix.lower()
        if suffix not in _SUPPORTED:
            supported = ", ".join(sorted(_SUPPORTED))
            return ToolResult(
                content=f"Error: unsupported file type '{suffix}' (supported: {supported})",
                is_error=True,
            )

        sheet = input_data.get("sheet")
        try:
            report = _check_file(target, sheet if isinstance(sheet, str) else None)
        except ImportError:
            return ToolResult(
                content=(
                    "Error: data_quality requires pandas (+openpyxl for Excel, "
                    "+pyarrow or fastparquet for Parquet). Install the 'data' "
                    "extra, plus pyarrow/fastparquet if you need Parquet."
                ),
                is_error=True,
            )
        except KeyError as exc:
            return ToolResult(
                content=f"Error: sheet not found in workbook: {exc.args[0]}",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(content=f"Error checking {target}: {exc}", is_error=True)

        return ToolResult(content=_render_file(report), metadata={"quality": report})
