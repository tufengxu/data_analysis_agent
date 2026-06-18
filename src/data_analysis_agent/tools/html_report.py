"""HtmlReportTool: render a structured analysis report as a self-contained H5 page.

The model supplies WHAT (title / summary / sections / ECharts options / tables
as structured JSON); this tool owns HOW (deterministic template rendering,
escaping, size caps, file placement). Charts are ECharts option objects passed
through verbatim — the model computes the underlying aggregates first via
python_analysis, so numbers in the report are kernel-exact, not hallucinated.

Security posture:
    * Output is confined to the artifact directory (fail-closed on escape).
    * All text fields, including table cells, are HTML-escaped via html.escape.
      Chart option objects are JSON-serialized with a ``</`` + JS line-terminator
      escape before embedding in <script>, so values can't terminate the tag.
      (Tables render as escaped HTML text, not inside any script block.)
    * ECharts loads from a configurable src: an http(s) URL becomes a script
      tag (CDN mode); a local file path is inlined for fully-offline reports.
"""

from __future__ import annotations

import html
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

DEFAULT_ECHARTS_SRC = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"

# Caps keep one report from flooding disk or freezing the browser.
MAX_SECTIONS = 30
MAX_TABLE_ROWS = 200
MAX_OPTION_CHARS = 2_000_000
DEFAULT_CHART_HEIGHT = 360
MIN_CHART_HEIGHT = 120
MAX_CHART_HEIGHT = 1200

# Windows device names; writing to e.g. "CON" gets silently redirected there.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

_PAGE = Template(
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
$echarts_tag
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: #f5f7fa; color: #2c3e50; line-height: 1.7;
}
.wrap { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
header { margin: 16px 0 24px; }
h1 { font-size: 26px; letter-spacing: .5px; }
.subtitle { color: #5d6d7e; margin-top: 4px; font-size: 15px; }
.meta { color: #95a5a6; font-size: 12px; margin-top: 8px; }
.card {
  background: #fff; border-radius: 10px; padding: 20px 24px; margin-bottom: 20px;
  box-shadow: 0 1px 3px rgba(30, 40, 60, .08);
}
.card h2 {
  font-size: 18px; margin-bottom: 10px; padding-left: 10px;
  border-left: 4px solid #4a7bd0;
}
.summary { border-left: 4px solid #2eaa76; }
.summary h2 { border-left: none; padding-left: 0; color: #2eaa76; }
.card p { margin: 8px 0; word-break: break-word; }
.chart { width: 100%; margin-top: 12px; }
.chart-caption { text-align: center; color: #7f8c8d; font-size: 13px; margin-top: 6px; }
.chart-fallback { color: #c0392b; text-align: center; padding: 24px 0; }
.tbl-wrap { overflow-x: auto; margin-top: 12px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { border: 1px solid #e3e8ee; padding: 6px 10px; text-align: left; }
th { background: #f0f4f8; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
.tbl-note { color: #95a5a6; font-size: 12px; margin-top: 4px; }
footer { text-align: center; color: #b0b8c0; font-size: 12px; margin-top: 32px; }
@media (max-width: 600px) {
  .wrap { padding: 12px 8px 32px; }
  .card { padding: 14px 14px; }
  h1 { font-size: 21px; }
}
</style>
</head>
<body>
<div class="wrap">
<header>
<h1>$title</h1>
$subtitle_html
<p class="meta">生成时间:$generated_at</p>
</header>
$summary_html
$sections_html
<footer>DataAnalysisAgent · ECharts 可视化报告</footer>
</div>
$charts_script
</body>
</html>
"""
)

_CHARTS_SCRIPT = Template(
    """<script>
(function () {
  "use strict";
  function render(id, option) {
    var el = document.getElementById(id);
    if (!el) { return; }
    if (typeof echarts === "undefined") {
      el.textContent = "图表渲染失败:ECharts 未能加载(离线环境请配置本地 echarts_src)";
      el.className = "chart chart-fallback";
      return;
    }
    var chart = echarts.init(el);
    chart.setOption(option);
    window.addEventListener("resize", function () { chart.resize(); });
  }
$render_calls
})();
</script>"""
)


def _escape_json_for_script(payload: Any) -> str:
    """Serialize to JSON safe for embedding inside a <script> block.

    ``</`` → ``<\\/`` prevents any string value (e.g. "</script>") from
    terminating the script tag early. U+2028/U+2029 are JS line terminators
    (illegal in string literals before ES2019) and must be escaped so the
    inline script parses on every engine.
    """
    return (
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _text_to_html(text: str) -> str:
    """Escape plain text and split blank-line-separated paragraphs."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs)


class HtmlReportTool(Tool):
    """Render a structured analysis report into a self-contained H5 HTML file."""

    def __init__(
        self,
        artifact_dir: str | Path | None = None,
        echarts_src: str = DEFAULT_ECHARTS_SRC,
    ) -> None:
        if artifact_dir is None:
            artifact_dir = Path(tempfile.mkdtemp(prefix="daa_reports_"))
        self.artifact_dir = Path(artifact_dir).expanduser().resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.echarts_src = echarts_src

    @property
    def name(self) -> str:
        return "html_report"

    @property
    def description(self) -> str:
        return (
            "Generate a self-contained H5 HTML analysis report with ECharts charts. "
            "Call this AFTER the analysis is done: first compute every number/series "
            "with python_analysis, then pass the results in as structured sections. "
            "Each section may carry one ECharts `option` object (rendered verbatim) "
            "and/or one small table. The report file path is returned for the user."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Report title"},
                "subtitle": {"type": "string", "description": "Optional subtitle"},
                "summary": {
                    "type": "string",
                    "description": "Executive summary (plain text, blank line = new paragraph)",
                },
                "sections": {
                    "type": "array",
                    "description": f"Report sections, at most {MAX_SECTIONS}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "text": {
                                "type": "string",
                                "description": "Section body (plain text)",
                            },
                            "chart": {
                                "type": "object",
                                "description": "Optional ECharts chart",
                                "properties": {
                                    "option": {
                                        "type": "object",
                                        "description": "Full ECharts option object",
                                    },
                                    "height": {
                                        "type": "integer",
                                        "description": (
                                            f"Pixel height (default {DEFAULT_CHART_HEIGHT})"
                                        ),
                                    },
                                    "caption": {"type": "string"},
                                },
                                "required": ["option"],
                            },
                            "table": {
                                "type": "object",
                                "description": "Optional small data table",
                                "properties": {
                                    "columns": {"type": "array", "items": {"type": "string"}},
                                    "rows": {"type": "array", "items": {"type": "array"}},
                                },
                                "required": ["columns", "rows"],
                            },
                        },
                        "required": ["heading"],
                    },
                },
                "file_name": {
                    "type": "string",
                    "description": "Optional file name (no directories); default auto-generated",
                },
            },
            "required": ["title", "sections"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return False

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return False  # writes the report file

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        title = input_data.get("title")
        if not title or not isinstance(title, str):
            return ValidationResult.fail("title is required and must be a string")

        sections = input_data.get("sections")
        if not isinstance(sections, list) or not sections:
            return ValidationResult.fail("sections must be a non-empty array")
        if len(sections) > MAX_SECTIONS:
            return ValidationResult.fail(f"too many sections (max {MAX_SECTIONS})")

        for idx, section in enumerate(sections):
            if not isinstance(section, dict):
                return ValidationResult.fail(f"sections[{idx}] must be an object")
            if not section.get("heading") or not isinstance(section.get("heading"), str):
                return ValidationResult.fail(f"sections[{idx}].heading is required")
            chart = section.get("chart")
            if chart is not None:
                err = self._validate_chart(idx, chart)
                if err:
                    return ValidationResult.fail(err)
            table = section.get("table")
            if table is not None:
                err = self._validate_table(idx, table)
                if err:
                    return ValidationResult.fail(err)

        file_name = input_data.get("file_name")
        if file_name is not None:
            if not isinstance(file_name, str) or not file_name:
                return ValidationResult.fail("file_name must be a non-empty string")
            # Fail-closed: a bare name only — the report never leaves artifact_dir.
            # NUL would slip past Path.name and blow up at resolve() instead.
            if "\x00" in file_name:
                return ValidationResult.fail("file_name must not contain NUL characters")
            if Path(file_name).name != file_name or file_name.startswith("."):
                return ValidationResult.fail("file_name must be a bare file name (no directories)")
            # Cross-platform hygiene: Windows strips trailing dots/spaces and
            # reserves device names, silently redirecting the write.
            if file_name.endswith((".", " ")):
                return ValidationResult.fail("file_name must not end with a dot or space")
            # strip() before matching: Windows also trims a basename's leading
            # and trailing whitespace, so "con .html" would still hit CON.
            stem = file_name.split(".", 1)[0].strip().upper()
            if stem in _WINDOWS_RESERVED_NAMES:
                return ValidationResult.fail(f"file_name '{file_name}' is a reserved device name")

        return ValidationResult.success()

    @staticmethod
    def _validate_chart(idx: int, chart: Any) -> str | None:
        if not isinstance(chart, dict):
            return f"sections[{idx}].chart must be an object"
        option = chart.get("option")
        if not isinstance(option, dict):
            return f"sections[{idx}].chart.option must be an ECharts option object"
        try:
            serialized = _escape_json_for_script(option)
        except (TypeError, ValueError) as e:
            return f"sections[{idx}].chart.option is not JSON-serializable: {e}"
        if len(serialized) > MAX_OPTION_CHARS:
            return (
                f"sections[{idx}].chart.option too large "
                f"({len(serialized)} chars > {MAX_OPTION_CHARS}); aggregate the data first"
            )
        height = chart.get("height", DEFAULT_CHART_HEIGHT)
        if not isinstance(height, int) or not (MIN_CHART_HEIGHT <= height <= MAX_CHART_HEIGHT):
            return (
                f"sections[{idx}].chart.height must be an integer in "
                f"[{MIN_CHART_HEIGHT}, {MAX_CHART_HEIGHT}]"
            )
        return None

    @staticmethod
    def _validate_table(idx: int, table: Any) -> str | None:
        if not isinstance(table, dict):
            return f"sections[{idx}].table must be an object"
        columns = table.get("columns")
        rows = table.get("rows")
        if not isinstance(columns, list) or not columns:
            return f"sections[{idx}].table.columns must be a non-empty array"
        if not isinstance(rows, list):
            return f"sections[{idx}].table.rows must be an array"
        for row_idx, row in enumerate(rows):
            if not isinstance(row, list):
                return f"sections[{idx}].table.rows items must be arrays"
            if len(row) != len(columns):
                return (
                    f"sections[{idx}].table.rows[{row_idx}] has {len(row)} cells "
                    f"but there are {len(columns)} columns"
                )
        return None

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        """Render and write the report.

        Contract: assumes ``input_data`` already passed ``validate_input``
        (the agent loop enforces validate-before-call); size/shape caps live
        there. Path containment is still re-checked here as defense in depth.
        """
        sections = input_data["sections"]
        page = self._render_page(input_data)

        file_name = input_data.get("file_name") or (
            f"report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            f"_{uuid.uuid4().hex[:6]}.html"
        )
        out_path = (self.artifact_dir / file_name).resolve()
        # Defense in depth on top of validate_input's bare-name rule.
        if not out_path.is_relative_to(self.artifact_dir):
            return ToolResult(
                content="Permission denied: report path escapes the artifact directory.",
                is_error=True,
            )
        try:
            out_path.write_text(page, encoding="utf-8")
        except OSError as e:
            return ToolResult(content=f"Failed to write report: {e}", is_error=True)

        chart_count = sum(1 for s in sections if isinstance(s, dict) and s.get("chart"))
        return ToolResult(
            content=(
                f"HTML 报告已生成:{len(sections)} 个章节,{chart_count} 张图表。"
                "文件路径见下方产物标注。"
            ),
            metadata={"artifact_paths": [str(out_path)]},
        )

    # --- rendering ---------------------------------------------------------

    def _render_page(self, data: dict[str, Any]) -> str:
        title = html.escape(str(data["title"]))
        subtitle = data.get("subtitle")
        subtitle_html = f'<p class="subtitle">{html.escape(str(subtitle))}</p>' if subtitle else ""
        summary = data.get("summary")
        summary_body = _text_to_html(str(summary)) if summary else ""
        summary_html = (
            '<div class="card summary"><h2>摘要</h2>' + summary_body + "</div>"
            if summary_body  # skip the card when summary is blank/whitespace-only
            else ""
        )

        sections_html_parts: list[str] = []
        render_calls: list[str] = []
        chart_idx = 0
        for section in data["sections"]:
            part, chart_idx = self._render_section(section, chart_idx, render_calls)
            sections_html_parts.append(part)

        charts_script = (
            _CHARTS_SCRIPT.substitute(render_calls="\n".join(render_calls)) if render_calls else ""
        )
        return _PAGE.substitute(
            title=title,
            echarts_tag=self._echarts_tag() if render_calls else "",
            subtitle_html=subtitle_html,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            summary_html=summary_html,
            sections_html="\n".join(sections_html_parts),
            charts_script=charts_script,
        )

    def _render_section(
        self,
        section: dict[str, Any],
        chart_idx: int,
        render_calls: list[str],
    ) -> tuple[str, int]:
        parts = ['<section class="card">']
        parts.append(f"<h2>{html.escape(str(section['heading']))}</h2>")
        text = section.get("text")
        if text:
            parts.append(_text_to_html(str(text)))

        chart = section.get("chart")
        if chart:
            height = chart.get("height", DEFAULT_CHART_HEIGHT)
            chart_id = f"chart_{chart_idx}"
            chart_idx += 1
            parts.append(f'<div class="chart" id="{chart_id}" style="height:{height}px"></div>')
            caption = chart.get("caption")
            if caption:
                parts.append(f'<p class="chart-caption">{html.escape(str(caption))}</p>')
            render_calls.append(
                f'  render("{chart_id}", {_escape_json_for_script(chart["option"])});'
            )

        table = section.get("table")
        if table:
            parts.append(self._render_table(table))

        parts.append("</section>")
        return "\n".join(parts), chart_idx

    @staticmethod
    def _render_table(table: dict[str, Any]) -> str:
        columns = table["columns"]
        rows = table["rows"]
        shown = rows[:MAX_TABLE_ROWS]
        head = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
            for row in shown
        )
        note = (
            f'<p class="tbl-note">表格仅展示前 {MAX_TABLE_ROWS} 行(共 {len(rows)} 行)</p>'
            if len(rows) > MAX_TABLE_ROWS
            else ""
        )
        return (
            '<div class="tbl-wrap"><table>'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody>"
            "</table></div>" + note
        )

    def _echarts_tag(self) -> str:
        """CDN URL → script src tag; local file path → inline embed (offline)."""
        src = self.echarts_src
        if src.startswith(("http://", "https://")):
            return f'<script src="{html.escape(src, quote=True)}"></script>'
        local = Path(src).expanduser()
        try:
            payload = local.read_text(encoding="utf-8")
        except OSError:
            # Fall back to the default CDN rather than shipping a chartless page.
            return f'<script src="{html.escape(DEFAULT_ECHARTS_SRC, quote=True)}"></script>'
        return "<script>" + payload.replace("</", "<\\/") + "</script>"
