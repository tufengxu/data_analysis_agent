"""L3 serialization: render summary dicts to compact, self-describing Markdown.

Single renderer consumed by both the sandbox path (exact stats) and the text
fallback (sample-estimated stats). Every block ends with an explicit sampling
caveat so the model does not infer totals from a sample (context-rot defense).
"""

from __future__ import annotations

import math
from typing import Any

_CELL_WIDTH = 40


def render_summary_dict(summary: dict[str, Any], *, stats_exact: bool = True) -> str:
    """Render a :class:`TableSummary`-shaped dict to Markdown."""
    n_rows = int(summary.get("n_rows", 0))
    n_cols = int(summary.get("n_cols", 0))
    method = summary.get("sampling_method", "")
    fidelity = summary.get("fidelity_level", "")

    lines: list[str] = ["### 数据采样摘要 (sampled view)"]
    lines.append(f"- rows={n_rows:,} · cols={n_cols} · method={method} · fidelity={fidelity}")

    columns = summary.get("columns", [])
    if columns:
        stat_label = "computed on full data" if stats_exact else "estimated from parsed sample"
        lines += ["", f"**列统计 ({stat_label}):**", ""]
        lines.append("| column | kind | non-null | nulls | stats |")
        lines.append("|---|---|---|---|---|")
        for col in columns:
            lines.append(
                f"| {_cell(col.get('name', ''))} | {col.get('kind', '')} | "
                f"{col.get('count', 0)} | {col.get('null_count', 0)} | "
                f"{_fmt_stats(col.get('stats', {}))} |"
            )

    sample_rows = summary.get("sample_rows", [])
    if sample_rows:
        lines += ["", f"**代表性样本行 ({len(sample_rows)} of {n_rows:,}):**", ""]
        lines += _rows_md(sample_rows)

    outlier_rows = summary.get("outlier_rows", [])
    if outlier_rows:
        lines += ["", f"**离群行 (IQR outliers, {len(outlier_rows)}):**", ""]
        lines += _rows_md(outlier_rows)

    for note in summary.get("notes", []):
        lines += ["", f"> {note}"]

    lines += [
        "",
        f"> ⚠ 本视图为 {n_rows:,} 行的采样/摘要;精确聚合(求和/计数/比率/去重)"
        "请在 pandas/SQL 内计算,勿据样本推断总量。",
    ]
    return "\n".join(lines)


def render_text_digest(digest: dict[str, Any]) -> str:
    """Render a non-tabular text digest to Markdown."""
    n_lines = int(digest.get("n_lines", 0))
    n_chars = int(digest.get("n_chars", 0))
    approx_unique = digest.get("n_unique_approx", "?")

    lines: list[str] = ["### 文本结果摘要 (sampled view)"]
    lines.append(f"- lines={n_lines:,} · chars={n_chars:,} · approx_unique_lines={approx_unique}")

    head = digest.get("head")
    if head:
        lines += ["", "**开头:**", "```", head, "```"]

    sampled = digest.get("sampled_lines") or []
    if sampled:
        lines += ["", f"**随机采样的 {len(sampled)} 行:**", "```", *sampled, "```"]

    tail = digest.get("tail")
    if tail:
        lines += ["", "**结尾:**", "```", tail, "```"]

    for note in digest.get("notes", []):
        lines += ["", f"> {note}"]

    lines += ["", f"> ⚠ 本视图为 {n_lines:,} 行文本的采样;完整内容已省略,勿据样本推断全量。"]
    return "\n".join(lines)


def _rows_md(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    cols: list[str] = []
    for row in rows:
        for key in row:
            if key not in cols:
                cols.append(key)
    out = [
        "| " + " | ".join(_cell(c) for c in cols) + " |",
        "|" + "|".join("---" for _ in cols) + "|",
    ]
    for row in rows:
        out.append("| " + " | ".join(_cell(row.get(c, "")) for c in cols) + " |")
    return out


def _cell(value: Any, width: int = _CELL_WIDTH) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt_stats(stats: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("min", "mean", "std", "max"):
        if stats.get(key) is not None:
            parts.append(f"{key}={_num(stats[key])}")
    quantiles = stats.get("quantiles")
    if quantiles:
        qs = ", ".join(f"p{_pct(p)}={_num(v)}" for p, v in quantiles)
        parts.append(f"q[{qs}]")
    if "n_outliers" in stats:
        parts.append(f"outliers={stats['n_outliers']}")
    if "cardinality" in stats:
        parts.append(f"card={stats['cardinality']}")
    top_k = stats.get("top_k")
    if top_k:
        rendered = ", ".join(f"{_cell(v, 16)}:{c}" for v, c in top_k[:5])
        parts.append(f"top=[{rendered}]")
    return "; ".join(parts) if parts else "—"


def _pct(prob: float) -> str:
    return str(int(round(float(prob) * 100)))


def _num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number) or math.isinf(number):
        return str(value)
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.4g}"
