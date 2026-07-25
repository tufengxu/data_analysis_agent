"""JoinPlannerTool: read-only cross-table join advisory.

The complement that sits between ``data_profile`` (which only previews column
names so the model can *spot* a possible shared key) and ``python_analysis``
(where the model actually merges). Today the model has to write exploratory
pandas to answer "what key joins these tables, is it unique, will rows
multiply, how many rows vanish on an inner join?" — guess-and-check that is
slow and silently multiplies rows when both sides are non-unique.

``join_planner`` answers that deterministically and read-only:

- candidate join keys = column names shared across ≥2 tables (exact match)
- per table/key: uniqueness, null count → relationship (1:1 / 1:N / N:1 / N:N)
- value coverage (will rows match?) and estimated joined-row count
- row-multiplication risk (N:N is the classic footgun)
- a recommended join order, anchored at the largest (fact) table, that prefers
  joining each new table on a key where the *incoming* table is unique — the
  star-schema rule that avoids accidental multiplication

Design constraints (mirror the existing data-read policy, same as
``data_quality``):
- Read-only, deterministic; never executes model code.
- ``allowed_paths`` fail-closed whitelist; ``resolve()`` before the whitelist
  check (symlink / ``..`` proof).
- ABSOLUTE paths in output.
- pandas hard-required (set-intersection / value_counts have no stdlib form);
  a clear error names the real deps otherwise.
- Full-table read (join semantics need real values), per-table 1,000,000-row
  cap reported honestly via ``n_truncated``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

_MAX_ROWS = 1_000_000
_MAX_TABLES = 20
# Above this key-cardinality, skip precise set overlap / value_counts work to
# bound memory; such columns rarely join and the relationship is still reported
# from uniqueness alone.
_MAX_OVERLAP_VALUES = 200_000
_LOW_OVERLAP = 0.5
_HIGH_NULL_PCT = 50.0

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


# --- readers (CSV/TSV degrade on empty; Parquet needs an engine) -------------


def _read_delimited(path: Path, sep: str) -> tuple[Any, bool]:
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

    return pd.read_parquet(path), False  # columnar read; no row cap applied


def _read_excel(path: Path) -> list[tuple[str, Any, bool]]:
    """Return [(sheet_name, df, truncated), ...] — one per sheet."""
    import pandas as pd

    workbook = pd.ExcelFile(path)
    out: list[tuple[str, Any, bool]] = []
    for sheet in workbook.sheet_names:
        frame = workbook.parse(sheet, nrows=_MAX_ROWS + 1)
        truncated = int(len(frame)) > _MAX_ROWS
        if truncated:
            frame = frame.iloc[:_MAX_ROWS]
        out.append((sheet, frame, truncated))
    return out


def _read_tables_for(path: Path) -> list[tuple[str | None, str, Any, bool]]:
    """[(sheet, format, df, truncated), ...] for one path."""
    suffix = path.suffix.lower()
    if suffix in _DELIMITERS:
        df, truncated = _read_delimited(path, _DELIMITERS[suffix])
        return [(None, _format_for(suffix), df, truncated)]
    if suffix in _PARQUET_SUFFIXES:
        df, truncated = _read_parquet(path)
        return [(None, _format_for(suffix), df, truncated)]
    if suffix in _EXCEL_SUFFIXES:
        fmt = _format_for(suffix)
        return [(sheet, fmt, df, truncated) for sheet, df, truncated in _read_excel(path)]
    raise ValueError(f"unsupported file type '{suffix}'")  # pragma: no cover - guarded


def _dedupe_names(names: list[str]) -> list[str]:
    """Unique table display names by suffixing a counter on collision."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for nm in names:
        if nm not in seen:
            seen[nm] = 0
            out.append(nm)
        else:
            seen[nm] += 1
            out.append(f"{nm}#{seen[nm]}")
    return out


# --- analysis ---------------------------------------------------------------


def _relationship(incoming_unique: bool, partner_unique: bool) -> str:
    """incoming : partner relationship from the two uniqueness flags."""
    if incoming_unique and partner_unique:
        return "1:1"
    if incoming_unique:
        return "1:N"  # incoming is the unique side (a dimension)
    if partner_unique:
        return "N:1"  # incoming is the many side
    return "N:N"


def _overlap(col_a: Any, col_b: Any) -> dict[str, Any]:
    """Set overlap of two key Series, with a high-cardinality guard."""
    n_unique_a = int(col_a.nunique(dropna=True))
    n_unique_b = int(col_b.nunique(dropna=True))
    if n_unique_a > _MAX_OVERLAP_VALUES or n_unique_b > _MAX_OVERLAP_VALUES:
        return {
            "overlap_count": None,
            "left_coverage": None,
            "right_coverage": None,
            "skipped": "high-cardinality",
        }
    set_a = set(col_a.dropna().tolist())
    set_b = set(col_b.dropna().tolist())
    inter = set_a & set_b
    la, lb = len(set_a), len(set_b)
    return {
        "overlap_count": len(inter),
        "left_coverage": round(len(inter) / la, 6) if la else 0.0,
        "right_coverage": round(len(inter) / lb, 6) if lb else 0.0,
        "skipped": None,
    }


def _estimated_join_rows(col_a: Any, col_b: Any) -> int | None:
    """Σ over the value intersection of freq_a(v)·freq_b(v).

    The expected row count of an equi-join on this key — correct for every
    relationship type. Returns None when overlap was skipped.
    """
    if (
        int(col_a.nunique(dropna=True)) > _MAX_OVERLAP_VALUES
        or int(col_b.nunique(dropna=True)) > _MAX_OVERLAP_VALUES
    ):
        return None
    counts_a = col_a.value_counts(dropna=True)
    counts_b = col_b.value_counts(dropna=True)
    common = counts_a.index.intersection(counts_b.index)
    if len(common) == 0:
        return 0
    a = counts_a.reindex(common, fill_value=0)
    b = counts_b.reindex(common, fill_value=0)
    return int((a * b).sum())


def _per_table_key_stats(df: Any, key: str) -> dict[str, Any]:
    col = df[key]
    n_rows = int(len(col))
    n_null = int(col.isna().sum())
    n_non_null = n_rows - n_null
    n_unique = int(col.nunique(dropna=True))
    is_unique = n_non_null > 0 and n_unique == n_non_null
    return {
        "n_rows": n_rows,
        "n_unique": n_unique,
        "n_null": n_null,
        "n_non_null": n_non_null,
        "is_unique": is_unique,
    }


def _find_link(
    name: str,
    ordered_set: set[str],
    candidate_keys: list[dict[str, Any]],
) -> tuple[str, str, str, str] | None:
    """Best candidate key connecting ``name`` to an already-ordered table.

    Prefers a key where ``name`` (the incoming table) is unique — joining a
    unique-side table onto the result does not multiply rows. Falls back to a
    non-unique key (flagged high risk) so the table still gets placed.
    """
    options: list[tuple[str, str, str, str, bool]] = []
    for ck in candidate_keys:
        if name not in ck["present_in"]:
            continue
        partners = sorted(p for p in ck["present_in"] if p in ordered_set)
        if not partners:
            continue
        partner = partners[0]
        incoming_unique = bool(ck["per_table"][name]["is_unique"])
        partner_unique = bool(ck["per_table"][partner]["is_unique"])
        rel = _relationship(incoming_unique, partner_unique)
        risk = "none" if incoming_unique else "high"
        options.append((ck["key"], partner, rel, risk, incoming_unique))
    if not options:
        return None
    options.sort(key=lambda o: (not o[4], o[0]))  # prefer unique-incoming, then key
    key, partner, rel, risk, _ = options[0]
    return key, partner, rel, risk


def _recommended_order(
    tables: list[dict[str, Any]],
    candidate_keys: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Greedy star-schema order: anchor at the largest table, add others on
    keys where the incoming table is unique (no multiplication)."""
    if not tables:
        return [], [], []
    by_size = sorted(tables, key=lambda t: (t["n_rows"], t["n_cols"]), reverse=True)
    base = by_size[0]["name"]
    ordered = [base]
    ordered_set = {base}
    steps: list[dict[str, Any]] = [
        {
            "table": base,
            "via_key": None,
            "join_with": None,
            "relationship": None,
            "risk": None,
            "note": "base (most rows)",
        }
    ]
    remaining = [t["name"] for t in by_size[1:]]
    warnings: list[str] = []

    progress = True
    while remaining and progress:
        progress = False
        for name in list(remaining):
            link = _find_link(name, ordered_set, candidate_keys)
            if link is None:
                continue
            key, partner, rel, risk = link
            ordered.append(name)
            ordered_set.add(name)
            remaining.remove(name)
            steps.append(
                {
                    "table": name,
                    "via_key": key,
                    "join_with": partner,
                    "relationship": rel,
                    "risk": risk,
                }
            )
            progress = True

    for name in remaining:  # shares no key with the joined set
        ordered.append(name)
        steps.append(
            {
                "table": name,
                "via_key": None,
                "join_with": None,
                "relationship": None,
                "risk": None,
                "note": "no shared-name key with joined tables",
            }
        )
        warnings.append(
            f"{name}: no shared-name candidate key with already-joined tables; "
            "needs an explicit key or value-level matching."
        )
    return ordered, steps, warnings


def _build_plan(tables: list[dict[str, Any]], dfs: dict[str, Any]) -> dict[str, Any]:
    # column -> set of table names that contain it (exact name match)
    col_tables: dict[str, set[str]] = {}
    for t in tables:
        for col in t["columns"]:
            col_tables.setdefault(col, set()).add(t["name"])
    candidate_names = sorted(c for c, tset in col_tables.items() if len(tset) >= 2)

    candidate_keys: list[dict[str, Any]] = []
    for key in candidate_names:
        present = sorted(col_tables[key])
        per_table = {nm: _per_table_key_stats(dfs[nm], key) for nm in present}
        pairs: list[dict[str, Any]] = []
        for i, left in enumerate(present):
            for right in present[i + 1 :]:
                col_l = dfs[left][key]
                col_r = dfs[right][key]
                overlap = _overlap(col_l, col_r)
                est = _estimated_join_rows(col_l, col_r)
                rel = _relationship(per_table[left]["is_unique"], per_table[right]["is_unique"])
                denom = max(per_table[left]["n_rows"], per_table[right]["n_rows"]) or 1
                factor = round(est / denom, 6) if est is not None else None
                # Multiplication risk is exactly "both sides non-unique" (N:N): if
                # either side is unique, its frequencies are all 1, so est ≤ the
                # other side's rows and factor ≤ 1 — no multiplication is possible.
                # ``factor`` is still reported for inspection.
                risk = "high" if rel == "N:N" else "none"
                pairs.append(
                    {
                        "left": left,
                        "right": right,
                        "relationship": rel,
                        "overlap_count": overlap["overlap_count"],
                        "left_coverage": overlap["left_coverage"],
                        "right_coverage": overlap["right_coverage"],
                        "overlap_skipped": overlap["skipped"],
                        "estimated_join_rows": est,
                        "multiplication_factor": factor,
                        "row_multiplication_risk": risk,
                    }
                )
        candidate_keys.append(
            {"key": key, "present_in": present, "per_table": per_table, "pairs": pairs}
        )

    order, steps, order_warnings = _recommended_order(tables, candidate_keys)

    warnings: list[str] = list(order_warnings)
    for t in tables:
        if t["n_truncated"]:
            warnings.append(
                f"{t['name']}: exceeded the {_MAX_ROWS:,} row cap; key stats "
                f"reflect the first {_MAX_ROWS:,} rows only — uniqueness and "
                "multiplication risk are provisional."
            )
    if not candidate_keys:
        warnings.append(
            "no shared-name candidate keys across tables; inspect columns and "
            "consider explicit keys or value-level matching."
        )
    for ck in candidate_keys:
        for pair in ck["pairs"]:
            if pair["row_multiplication_risk"] == "high":
                warnings.append(
                    f"{ck['key']}: {pair['left']}↔{pair['right']} is N:N "
                    f"(factor {pair['multiplication_factor']}); joining two "
                    "non-unique sides explodes the row count."
                )
            lcov, rcov = pair["left_coverage"], pair["right_coverage"]
            if (
                lcov is not None
                and rcov is not None
                and (lcov < _LOW_OVERLAP or rcov < _LOW_OVERLAP)
            ):
                warnings.append(
                    f"{ck['key']}: low value overlap between {pair['left']} and "
                    f"{pair['right']} ({lcov:.0%}/{rcov:.0%}); same name but may "
                    "not be a real join key."
                )
        for nm, stats in ck["per_table"].items():
            if stats["n_rows"] and stats["n_null"] / stats["n_rows"] * 100 >= _HIGH_NULL_PCT:
                warnings.append(
                    f"{ck['key']}: ≥{_HIGH_NULL_PCT:.0f}% null in {nm} "
                    f"({stats['n_null']}/{stats['n_rows']}); null keys drop on join."
                )

    return {
        "tables": tables,
        "candidate_keys": candidate_keys,
        "recommended_order": order,
        "join_steps": steps,
        "warnings": warnings,
    }


def _render(plan: dict[str, Any]) -> str:
    tables = plan["tables"]
    candidates = plan["candidate_keys"]
    lines = [f"Join plan: {len(tables)} tables · {len(candidates)} candidate key(s)"]
    lines.append("  Tables:")
    for t in tables:
        trunc = " (truncated)" if t["n_truncated"] else ""
        lines.append(
            f"    - {t['name']}  [{t['format']}]  {t['n_rows']} rows x {t['n_cols']} cols{trunc}"
        )
    if candidates:
        lines.append("  Candidate keys:")
        for ck in candidates:
            lines.append(f"    - {ck['key']}  (in {', '.join(ck['present_in'])})")
            for pair in ck["pairs"]:
                est = (
                    "est ?"
                    if pair["estimated_join_rows"] is None
                    else f"est {pair['estimated_join_rows']}"
                )
                cov = (
                    f"{pair['left_coverage']:.0%}/{pair['right_coverage']:.0%}"
                    if pair["left_coverage"] is not None
                    else "overlap skipped"
                )
                lines.append(
                    f"        {pair['left']} → {pair['right']}: {pair['relationship']} · "
                    f"coverage {cov} · {est} rows · risk {pair['row_multiplication_risk']}"
                )
    else:
        lines.append("  Candidate keys: none (no shared column names)")

    steps = plan["join_steps"]
    if steps:
        parts = [steps[0]["table"]]
        for st in steps[1:]:
            note = st.get("note")
            if note:
                parts.append(f"{st['table']} ({note})")
            else:
                parts.append(
                    f"{st['table']} (via {st['via_key']}, {st['relationship']}, risk {st['risk']})"
                )
        lines.append("  Recommended order: " + " → ".join(parts))

    if plan["warnings"]:
        lines.append("  Warnings:")
        for w in plan["warnings"]:
            lines.append(f"    - {w}")
    return "\n".join(lines)


class JoinPlannerTool(Tool):
    """Plan joins across multiple tables before writing merge code.

    Inspects 2+ tables (multiple files, or one multi-sheet Excel workbook, or a
    mix) and reports candidate join keys (shared column names), each key's
    uniqueness per table, the resulting relationship (1:1 / 1:N / N:1 / N:N),
    value coverage, estimated joined-row count, row-multiplication risk (N:N is
    the classic footgun), null-key risk, and a recommended join order anchored
    at the largest table. Use data_profile first for STRUCTURE, then join_planner
    to decide keys safely, then merge in python_analysis. Read-only.
    """

    def __init__(self, allowed_paths: list[str | Path] | None = None) -> None:
        self.allowed_paths = [
            Path(p).expanduser().resolve() for p in (allowed_paths or [Path.cwd()])
        ]

    @property
    def name(self) -> str:
        return "join_planner"

    @property
    def description(self) -> str:
        return (
            "Plan joins across 2+ local tables before writing merge code. Pass a "
            "list of file paths (.csv/.tsv/.parquet/.xlsx/.xls); an Excel workbook "
            "contributes one table per sheet. Reports shared-column candidate join "
            "keys, per-key uniqueness, relationship (1:1/1:N/N:1/N:N), value "
            "coverage, estimated joined-row count, row-multiplication risk (N:N), "
            "null-key risk, and a recommended join order. Use data_profile for "
            "structure first, then this to pick keys safely."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "≥1 data file path. Each file becomes one table (Excel: "
                        "one per sheet). At least 2 tables are required to plan "
                        "a join — pass 2+ files, or one multi-sheet workbook."
                    ),
                },
            },
            "required": ["paths"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        paths = input_data.get("paths")
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(p, str) and p for p in paths)
        ):
            return ValidationResult.fail(
                "paths is required and must be a non-empty list of strings"
            )
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
        raw_paths = input_data["paths"]

        resolved: list[Path] = []
        for p in raw_paths:
            target = Path(p).expanduser().resolve()
            if not self._within_allowed(target):
                return ToolResult(
                    content=f"Error: path is outside allowed analysis paths: {p}",
                    is_error=True,
                )
            if not target.exists():
                return ToolResult(content=f"Error: path not found: {target}", is_error=True)
            if target.suffix.lower() not in _SUPPORTED:
                supported = ", ".join(sorted(_SUPPORTED))
                return ToolResult(
                    content=(
                        f"Error: unsupported file type '{target.suffix.lower()}' "
                        f"(supported: {supported})"
                    ),
                    is_error=True,
                )
            resolved.append(target)

        # Read every path into (source, raw_name, sheet, fmt, df, truncated) rows.
        rows: list[tuple[str, str, str | None, str, Any, bool]] = []
        for target in resolved:
            try:
                tables_for = _read_tables_for(target)
            except ImportError:
                return ToolResult(
                    content=(
                        "Error: join_planner requires pandas (+openpyxl for Excel, "
                        "+pyarrow or fastparquet for Parquet). Install the 'data' "
                        "extra, plus pyarrow/fastparquet if you need Parquet."
                    ),
                    is_error=True,
                )
            except Exception as exc:  # corrupt/encoded/malformed file → clear per-file error
                return ToolResult(content=f"Error reading {target}: {exc}", is_error=True)
            for sheet, fmt, df, truncated in tables_for:
                raw_name = target.name if sheet is None else f"{target.name}::{sheet}"
                rows.append((str(target), raw_name, sheet, fmt, df, truncated))

        unique_names = _dedupe_names([r[1] for r in rows])
        tables: list[dict[str, Any]] = []
        dfs: dict[str, Any] = {}
        for (source, _raw, sheet, fmt, df, truncated), nm in zip(rows, unique_names, strict=True):
            tables.append(
                {
                    "name": nm,
                    "source": source,
                    "format": fmt,
                    "sheet": sheet,
                    "n_rows": int(len(df)),
                    "n_cols": int(df.shape[1]),
                    "n_truncated": truncated,
                    "columns": [str(c) for c in df.columns],
                }
            )
            dfs[nm] = df

        excess = 0
        if len(tables) > _MAX_TABLES:
            excess = len(tables) - _MAX_TABLES
            kept = {t["name"] for t in tables[:_MAX_TABLES]}
            tables = tables[:_MAX_TABLES]
            dfs = {nm: df for nm, df in dfs.items() if nm in kept}

        if len(tables) < 2:
            return ToolResult(
                content=(
                    "Error: need ≥2 tables to plan a join; "
                    f"got {len(tables)} (pass 2+ files, or one multi-sheet workbook)."
                ),
                is_error=True,
            )

        plan = _build_plan(tables, dfs)
        if excess:
            plan["warnings"].append(
                f"more than {_MAX_TABLES} tables; analyzed the first "
                f"{_MAX_TABLES} and skipped {excess}."
            )
        return ToolResult(content=_render(plan), metadata={"join_plan": plan})
