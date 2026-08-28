"""In-harness fallback summarizer — pure stdlib (pandas optional, not required).

Replaces the blind head-truncation at the tool-result seam. Detects tabular
text (Markdown table / CSV / whitespace-aligned), reparses it, and produces a
representative sample plus sample-estimated stats. Non-tabular text degrades to
a line-level reservoir sample with near-duplicate dedup. Any failure degrades
further to head+tail truncation — never worse than the original behavior.
"""

from __future__ import annotations

import bisect as _bisect
import csv as _csv
import random
import re
from collections import Counter
from typing import Any

from . import render
from .config import SamplingConfig
from .model import ColumnSummary, TableSummary

_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_NULL_TOKENS = {"", "nan", "none", "null", "na", "n/a", "<na>"}
# retrieve_result pages start with this header marker; they are deliberate
# raw-recall fetches and must never be re-summarized (D2).
_RECALL_PAGE_PREFIX = "[result_id="


def _effective_trigger(config: SamplingConfig, pressure: float) -> int:
    """D2: the trigger lowers linearly with context pressure, floored."""
    scaled = int(config.trigger_chars * (1 - config.trigger_pressure_scale * pressure))
    return max(config.trigger_floor_chars, scaled)


def compact_result(
    content: str,
    max_chars: int,
    config: SamplingConfig | None = None,
    context_pressure: float = 0.0,
) -> tuple[str, bool]:
    """Compact an oversized tool result with pressure-adaptive gain gating.

    Returns ``(content, was_compacted)``. Retrieved result pages (content
    starting with the ``[result_id=`` page header) pass through untouched —
    they are explicit raw-recall actions. Results at or below the
    pressure-scaled effective trigger pass through unchanged. After
    summarizing, the summary replaces the original only if it is short enough
    relative to an acceptance ratio that scales with ``context_pressure``
    (0=empty→strict, 1=near full→lenient) — unless the original exceeds
    ``max_chars`` (which would otherwise be truncated), in which case
    compaction is forced.
    """
    config = config or SamplingConfig()
    pressure = min(1.0, max(0.0, context_pressure))
    if content.startswith(_RECALL_PAGE_PREFIX):
        return content, False
    if len(content) <= _effective_trigger(config, pressure):
        return content, False
    try:
        out = summarize_text(content, config)
    except Exception:
        out = _head_tail_truncate(content, config.trigger_chars)

    accept_ratio = (
        config.gate_ratio_low_pressure
        + (config.gate_ratio_high_pressure - config.gate_ratio_low_pressure) * pressure
    )
    fits_within_cap = len(content) <= max_chars
    if len(out) > len(content) * accept_ratio and fits_within_cap:
        return content, False  # gain too small and original fits -> keep original

    if max_chars and len(out) > max_chars:
        out = _head_tail_truncate(out, max_chars)
    return out, True


def summarize_text(text: str, config: SamplingConfig | None = None) -> str:
    """Summarize arbitrary text; tabular when detectable, else a text digest."""
    config = config or SamplingConfig()
    parsed = detect_table(text)
    if parsed is not None:
        headers, rows = parsed
        summary = summarize_table_rows(headers, rows, config)
        return render.render_summary_dict(summary.to_dict(), stats_exact=False)
    return render.render_text_digest(_text_digest(text, config))


# --------------------------------------------------------------------------- #
# Table detection / parsing
# --------------------------------------------------------------------------- #
def detect_table(text: str) -> tuple[list[str], list[list[str]]] | None:
    """Detect a Markdown / CSV / whitespace table; return (headers, rows)."""
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if len(lines) < 3:
        return None
    return _parse_markdown_table(lines) or _parse_csv(lines) or _parse_whitespace(lines)


def _split_pipe(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_markdown_table(lines: list[str]) -> tuple[list[str], list[list[str]]] | None:
    sep_idx = None
    for i, line in enumerate(lines):
        if "|" in line and set(line.strip()) <= set("|-: "):
            sep_idx = i
            break
    if not sep_idx:  # None or 0 (no header above)
        return None
    headers = _split_pipe(lines[sep_idx - 1])
    if len(headers) < 2:
        return None
    rows = [
        cells
        for line in lines[sep_idx + 1 :]
        if "|" in line
        for cells in [_split_pipe(line)]
        if len(cells) == len(headers)
    ]
    return (headers, rows) if rows else None


def _parse_csv(lines: list[str]) -> tuple[list[str], list[list[str]]] | None:
    if not any("," in line for line in lines[:50]):
        return None
    try:
        reader = [row for row in _csv.reader(lines) if row]
    except _csv.Error:
        return None
    if not reader:
        return None
    ncol, freq = Counter(len(row) for row in reader).most_common(1)[0]
    if ncol < 2 or freq < max(3, 0.6 * len(reader)):
        return None
    rows = [row for row in reader if len(row) == ncol]
    return (rows[0], rows[1:]) if len(rows) > 1 else None


def _parse_whitespace(lines: list[str]) -> tuple[list[str], list[list[str]]] | None:
    split_lines = [re.split(r"\s{2,}", line.strip()) for line in lines]
    ncol, freq = Counter(len(parts) for parts in split_lines).most_common(1)[0]
    if ncol < 2 or freq < max(4, 0.7 * len(split_lines)):
        return None
    rows = [parts for parts in split_lines if len(parts) == ncol]
    return (rows[0], rows[1:]) if len(rows) > 3 else None


# --------------------------------------------------------------------------- #
# Tabular summary (sample-estimated stats)
# --------------------------------------------------------------------------- #
def summarize_table_rows(
    headers: list[str],
    rows: list[list[str]],
    config: SamplingConfig,
) -> TableSummary:
    rng = random.Random(config.seed)
    n_rows, n_cols = len(rows), len(headers)
    columns_by_index = list(zip(*rows, strict=False)) if rows else [() for _ in headers]

    column_summaries: list[ColumnSummary] = []
    numeric_columns: dict[int, list[float]] = {}
    notes: list[str] = []
    for idx, name in enumerate(headers):
        values = list(columns_by_index[idx]) if idx < len(columns_by_index) else []
        null_count = sum(1 for v in values if str(v).strip().lower() in _NULL_TOKENS)
        kind, parsed = _classify_column(values)
        stats: dict[str, Any] = {}
        if kind == "numeric":
            numeric_columns[idx] = parsed
            stats = _numeric_stats(parsed, config)
            non_null = len(values) - null_count
            if len(parsed) == non_null and stats["cardinality"] == non_null and non_null > 50:
                notes.append(f"列 {name} 每值唯一,疑似标识符(identifier-like)。")
        elif kind == "categorical":
            counter = Counter(parsed)
            stats = {
                "cardinality": len(counter),
                "top_k": [[v, n] for v, n in counter.most_common(config.top_k)],
                "tail_truncated": len(counter) > config.top_k,
            }
        column_summaries.append(
            ColumnSummary(
                name=str(name),
                kind=kind,
                count=len(values) - null_count,
                null_count=null_count,
                stats=stats,
            )
        )

    row_dicts = [dict(zip(headers, row, strict=False)) for row in rows]
    sample_rows, method = _sample_rows(row_dicts, rows, headers, numeric_columns, config, rng)
    outlier_rows = _outlier_rows(row_dicts, numeric_columns, headers, config)

    if n_rows > len(sample_rows):
        notes.append("列统计为解析样本的估算值(非精确);如需精确值请在 pandas 内重算。")

    return TableSummary(
        n_rows=n_rows,
        n_cols=n_cols,
        columns=column_summaries,
        sample_rows=sample_rows,
        outlier_rows=outlier_rows,
        sampling_method=method,
        fidelity_level=config.fidelity_level,
        notes=notes,
        truncated=n_rows > len(sample_rows),
    )


def _classify_column(values: list[str]) -> tuple[str, list[Any]]:
    non_null = [v for v in values if str(v).strip().lower() not in _NULL_TOKENS]
    if not non_null:
        return "other", []
    numbers = [float(v) for v in non_null if _NUM_RE.match(str(v).strip())]
    if len(numbers) >= 0.7 * len(non_null):
        return "numeric", numbers
    return "categorical", non_null


def _numeric_stats(numbers: list[float], config: SamplingConfig) -> dict[str, Any]:
    if not numbers:
        return {}
    ordered = sorted(numbers)
    n = len(ordered)
    mean = sum(ordered) / n
    variance = sum((x - mean) ** 2 for x in ordered) / n if n > 1 else 0.0
    quantiles = [[p, _quantile(ordered, p)] for p in config.quantiles]
    q1, q3 = _quantile(ordered, 0.25), _quantile(ordered, 0.75)
    iqr = q3 - q1
    n_outliers = sum(1 for x in ordered if x < q1 - 1.5 * iqr or x > q3 + 1.5 * iqr)
    return {
        "min": ordered[0],
        "max": ordered[-1],
        "mean": mean,
        "std": variance**0.5,
        "quantiles": quantiles,
        "histogram": _histogram(ordered, 8),
        "cardinality": len(set(ordered)),
        "n_outliers": n_outliers,
    }


def _histogram(ordered: list[float], n_bins: int = 8) -> list[int]:
    """Equi-depth bucket counts from the already-sorted value list."""
    n = len(ordered)
    if n == 0:
        return []
    edges = sorted({_quantile(ordered, i / n_bins) for i in range(n_bins + 1)})
    if len(edges) < 2:
        return [n]
    counts = [0] * (len(edges) - 1)
    for x in ordered:
        pos = min(max(_bisect.bisect_right(edges, x) - 1, 0), len(edges) - 2)
        counts[pos] += 1
    return counts


def _quantile(ordered: list[float], prob: float) -> float:
    n = len(ordered)
    if n == 1:
        return ordered[0]
    pos = prob * (n - 1)
    low = int(pos)
    high = min(low + 1, n - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def _sample_rows(
    row_dicts: list[dict[str, Any]],
    rows: list[list[str]],
    headers: list[str],
    numeric_columns: dict[int, list[float]],
    config: SamplingConfig,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], str]:
    k = min(config.max_sample_rows, len(row_dicts))
    if k <= 0:
        return [], "none"

    strat_idx = None
    if config.stratify == "auto":
        for idx in range(len(headers)):
            if idx in numeric_columns:
                continue
            column = [row[idx] for row in rows if idx < len(row)]
            cardinality = len(set(column))
            if 2 <= cardinality <= 10 and cardinality < len(column):
                strat_idx = idx
                break

    if strat_idx is None:
        return _reservoir(row_dicts, k, rng), "reservoir"

    groups: dict[Any, list[dict[str, Any]]] = {}
    for row_dict, row in zip(row_dicts, rows, strict=False):
        groups.setdefault(row[strat_idx], []).append(row_dict)
    total = len(row_dicts)
    sample: list[dict[str, Any]] = []
    for members in groups.values():
        share = min(max(1, round(k * len(members) / total)), len(members))
        sample.extend(_reservoir(members, share, rng))
    if len(sample) > k:
        sample = _reservoir(sample, k, rng)
    return sample, f"stratified[{headers[strat_idx]}]"


def _outlier_rows(
    row_dicts: list[dict[str, Any]],
    numeric_columns: dict[int, list[float]],
    headers: list[str],
    config: SamplingConfig,
) -> list[dict[str, Any]]:
    """IQR outlier rows, round-robin across ALL numeric columns.

    Each returned row carries an ``outlier_col`` key naming the flagging
    column (mirrors the sandbox path so both render identically).
    """
    if not config.include_outliers or not numeric_columns or config.max_outlier_rows <= 0:
        return []
    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    for idx in numeric_columns:
        name = headers[idx]
        ordered = sorted(numeric_columns[idx])
        q1, q3 = _quantile(ordered, 0.25), _quantile(ordered, 0.75)
        iqr = q3 - q1
        low_fence, high_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        hits = []
        for row_dict in row_dicts:
            raw = str(row_dict.get(name, "")).strip()
            if _NUM_RE.match(raw):
                value = float(raw)
                if value < low_fence or value > high_fence:
                    hits.append(row_dict)
        if hits:
            candidates.append((name, hits))

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    cursors = [0] * len(candidates)
    while len(rows) < config.max_outlier_rows:
        picked = False
        for ci, (name, hits) in enumerate(candidates):
            if len(rows) >= config.max_outlier_rows:
                break
            while cursors[ci] < len(hits):
                row_dict = hits[cursors[ci]]
                cursors[ci] += 1
                key = id(row_dict)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({**row_dict, "outlier_col": name})
                picked = True
                break
        if not picked:
            break
    return rows


# --------------------------------------------------------------------------- #
# Non-tabular text digest
# --------------------------------------------------------------------------- #
def _text_digest(text: str, config: SamplingConfig) -> dict[str, Any]:
    rng = random.Random(config.seed)
    raw_lines = text.splitlines()
    seen: set[str] = set()
    unique: list[str] = []
    for line in raw_lines:
        key = _normalize_line(line)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(line)

    k = min(config.max_sample_rows, len(unique))
    sampled = _reservoir(unique, k, rng) if unique else []
    notes: list[str] = []
    if len(unique) < len(raw_lines):
        notes.append(f"近重复行已去重:{len(raw_lines)} → {len(unique)}。")

    return {
        "n_lines": len(raw_lines),
        "n_chars": len(text),
        "n_unique_approx": len(seen),
        "sampled_lines": sampled,
        "head": "\n".join(raw_lines[:5]),
        "tail": "\n".join(raw_lines[-5:]),
        "notes": notes,
    }


def _normalize_line(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def _reservoir(items: list[Any], k: int, rng: random.Random) -> list[Any]:
    """Vitter's reservoir sampling — order-preserving for the chosen indices."""
    if k >= len(items):
        return list(items)
    reservoir = list(items[:k])
    for i in range(k, len(items)):
        j = rng.randint(0, i)
        if j < k:
            reservoir[j] = items[i]
    return reservoir


def _head_tail_truncate(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    half = max(0, (max_chars - 80) // 2)
    dropped = len(content) - 2 * half
    return (
        content[:half]
        + f"\n... [truncated {dropped} chars; head+tail kept] ...\n"
        + content[-half:]
    )
