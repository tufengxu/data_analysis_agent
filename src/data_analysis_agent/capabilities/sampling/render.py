"""L3 serialization: render summary dicts to compact, self-describing Markdown.

Single renderer consumed by both the sandbox path (exact stats) and the text
fallback (sample-estimated stats). Every block ends with an explicit sampling
caveat so the model does not infer totals from a sample (context-rot defense).
"""

from __future__ import annotations

import math
from typing import Any

_CELL_WIDTH = 40


def render_summary_dict(
    summary: dict[str, Any],
    *,
    stats_exact: bool = True,
    variable: str | None = None,
) -> str:
    """Render a :class:`TableSummary`-shaped dict to Markdown.

    ``variable`` names the kernel variable the table came from (P1-1
    provenance); omitted for anonymous results — output stays identical to
    the pre-variable era.
    """
    n_rows = int(summary.get("n_rows", 0))
    n_cols = int(summary.get("n_cols", 0))
    method = summary.get("sampling_method", "")
    fidelity = summary.get("fidelity_level", "")

    title = (
        f"### {variable} · 数据采样摘要 (sampled view)"
        if variable
        else "### 数据采样摘要 (sampled view)"
    )
    lines: list[str] = [title]
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
                f"{_num(col.get('count', 0))} | {_num(col.get('null_count', 0))} | "
                f"{_fmt_stats(col.get('stats', {}), base=col.get('count'))} |"
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


def render_json_digest(digest: dict[str, Any]) -> str:
    """Render a JSON structural digest (D7) to Markdown."""
    n_items = int(digest.get("n_items", 0))
    lines: list[str] = ["### JSON 结构摘要 (sampled view)"]
    lines.append(f"- items={n_items:,}")

    paths = digest.get("paths") or []
    if paths:
        lines += ["", "**键路径 (深度≤3):**", "", "| path | type | count |", "|---|---|---|"]
        for entry in paths:
            lines.append(
                f"| {_cell(entry.get('path', ''))} | {entry.get('type', '')} | "
                f"{_num(entry.get('count', 0))} |"
            )

    arrays = digest.get("arrays") or []
    if arrays:
        lines += ["", "**数组长度分布:**", ""]
        for entry in arrays:
            lines.append(
                f"- {entry.get('path', '')}[]: min={entry.get('min')} "
                f"中位={_num(entry.get('median', 0))} max={entry.get('max')}"
            )

    sampled = digest.get("sampled") or []
    if sampled:
        lines += ["", f"**代表元素 ({len(sampled)} of {n_items:,}):**", "```", *sampled, "```"]

    lines += ["", f"> ⚠ 本视图为 {n_items:,} 个 JSON 对象的结构采样;完整内容已省略。"]
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
    if isinstance(value, bool):
        text = str(value)
    elif isinstance(value, int | float):
        text = _num(value)
    else:
        text = str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt_stats(stats: dict[str, Any], base: int | None = None) -> str:
    parts: list[str] = []
    for key in ("min", "mean", "std", "max"):
        if stats.get(key) is not None:
            parts.append(f"{key}={_num(stats[key])}")
    quantiles = stats.get("quantiles")
    if quantiles:
        qs = ", ".join(f"p{_pct(p)}={_num(v)}" for p, v in quantiles)
        parts.append(f"q[{qs}]")
    histogram = stats.get("histogram")
    if histogram:
        parts.append("hist=[" + ",".join(_num(b) for b in histogram) + "]")
    if "granularity" in stats:
        parts.append(f"gran={stats['granularity']}")
    if "span_days" in stats:
        parts.append(f"span={_num(stats['span_days'])}d")
    if "n_outliers" in stats:
        parts.append(f"outliers={stats['n_outliers']}")
    if "cardinality" in stats:
        parts.append(f"card={stats['cardinality']}")
    top_k = stats.get("top_k")
    if top_k:
        rendered = ", ".join(f"{_cell(v, 16)}:{_share(c, base)}" for v, c in top_k[:5])
        parts.append(f"top=[{rendered}]")
    return "; ".join(parts) if parts else "—"


def _share(count: int, base: int | None) -> str:
    if base and base > 0:
        return f"{count}({round(100 * count / base)}%)"
    return str(count)


def _pct(prob: float) -> str:
    return str(int(round(float(prob) * 100)))


def _num(value: Any) -> str:
    """D1 呈现契约:千分位分组 + 3 位有效数字,常见量级不用科学计数法。

    千分位强制 tokenizer 右到左分组(算术准确率增益,arXiv:2402.14903);
    仅影响渲染文本,summary 数据结构中的数值保持原精度。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number) or math.isinf(number):
        return str(value)
    if number == 0:
        return "0"
    if number == int(number) and abs(number) < 1e15:
        return f"{int(number):,}"
    magnitude = abs(number)
    if 1e-3 <= magnitude < 1e15:
        decimals = 2 - math.floor(math.log10(magnitude))
        text = f"{round(number, decimals):,.{max(decimals, 0)}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    return f"{number:.3g}"
