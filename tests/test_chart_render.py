"""Wave 5 chart_render: 各 family option + 充分性 + fallback + artifact + 路径防护 + v2 组合。"""

from __future__ import annotations

import json
from pathlib import Path

from data_analysis_agent.reporting.chart_rules import MIN_SCATTER_POINTS, MIN_TREND_POINTS
from data_analysis_agent.tools.chart_render import ChartRenderTool
from data_analysis_agent.tools.html_report import HtmlReportTool


def _tool(tmp_path: Path) -> ChartRenderTool:
    return ChartRenderTool(artifact_dir=tmp_path)


# ----------------------------- 校验 -----------------------------


def test_validates_block_id_required(tmp_path: Path):
    tool = _tool(tmp_path)
    assert not tool.validate_input({}).valid
    assert not tool.validate_input(
        {"block_id": "  ", "family": "line", "data": {"labels": [], "series": []}}
    ).valid


def test_validates_family(tmp_path: Path):
    tool = _tool(tmp_path)
    assert not tool.validate_input(
        {
            "block_id": "c1",
            "family": "heatmap",
            "data": {"labels": ["a"], "series": [{"values": [1]}]},
        }
    ).valid


def test_validates_data_shape_line(tmp_path: Path):
    tool = _tool(tmp_path)
    assert not tool.validate_input(
        {"block_id": "c1", "family": "line", "data": {"series": [{"values": [1]}]}}
    ).valid  # 缺 labels
    assert not tool.validate_input(
        {"block_id": "c1", "family": "line", "data": {"labels": ["a"]}}
    ).valid  # 缺 series


def test_validates_data_shape_scatter(tmp_path: Path):
    tool = _tool(tmp_path)
    assert not tool.validate_input(
        {"block_id": "c1", "family": "scatter", "data": {"labels": ["a"]}}
    ).valid  # scatter 需 points


def test_validates_scatter_points_pair_shape(tmp_path: Path):
    tool = _tool(tmp_path)
    # points 元素必须是 [x,y] pair(评审 Medium:标量会被 ECharts 误读为 1D)
    assert not tool.validate_input(
        {"block_id": "c1", "family": "scatter", "data": {"points": [1, 2, 3]}}
    ).valid
    assert not tool.validate_input(
        {"block_id": "c1", "family": "scatter", "data": {"points": [[1, 2, 3]]}}
    ).valid  # 长度 3
    assert tool.validate_input(
        {"block_id": "c1", "family": "scatter", "data": {"points": [[1, 2]]}}
    ).valid


def test_validates_values_labels_length_mismatch(tmp_path: Path):
    tool = _tool(tmp_path)
    # values 与 labels 长度不一致 → 拒(评审 Medium:静默错位)
    assert not tool.validate_input(
        {
            "block_id": "c1",
            "family": "bar",
            "data": {"labels": ["a", "b", "c"], "series": [{"name": "x", "values": [1, 2]}]},
        }
    ).valid


def test_validates_empty_labels_rejected(tmp_path: Path):
    tool = _tool(tmp_path)
    assert not tool.validate_input(
        {"block_id": "c1", "family": "bar", "data": {"labels": [], "series": [{"values": []}]}}
    ).valid


def test_validates_file_name_must_be_json(tmp_path: Path):
    tool = _tool(tmp_path)
    res = tool.validate_input(
        {
            "block_id": "c1",
            "family": "bar",
            "data": {"labels": ["a"], "series": [{"values": [1]}]},
            "file_name": "gmv.txt",
        }
    )
    assert not res.valid


def test_block_id_bare_name_rejects(tmp_path: Path):
    tool = _tool(tmp_path)
    data = {"labels": ["a"], "series": [{"name": "s", "values": [1]}]}
    for bad in ["../evil", "a/b", "CON", "x.", "a b.", "  "]:
        res = tool.validate_input({"block_id": bad, "family": "line", "data": data})
        assert not res.valid, f"{bad!r} 应被拒"


# ----------------------------- family option 生成 -----------------------------


async def test_line_option(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "line",
            "data": {"labels": ["a", "b", "c"], "series": [{"name": "GMV", "values": [1, 2, 3]}]},
        }
    )
    opt = result.metadata["chart_option"]
    assert opt["xAxis"]["type"] == "category"
    assert opt["xAxis"]["data"] == ["a", "b", "c"]
    assert opt["series"][0]["type"] == "line"
    assert opt["series"][0]["data"] == [1, 2, 3]


async def test_bar_option(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "bar",
            "data": {"labels": ["a", "b"], "series": [{"name": "x", "values": [5, 7]}]},
        }
    )
    assert result.metadata["chart_option"]["series"][0]["type"] == "bar"


async def test_grouped_bar_multi_series(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "grouped_bar",
            "data": {
                "labels": ["a", "b"],
                "series": [{"name": "A", "values": [1, 2]}, {"name": "B", "values": [3, 4]}],
            },
        }
    )
    series = result.metadata["chart_option"]["series"]
    assert len(series) == 2
    assert all(s["type"] == "bar" for s in series)
    assert all("stack" not in s for s in series)


async def test_stacked_bar_has_stack(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "stacked_bar",
            "data": {
                "labels": ["a"],
                "series": [{"name": "A", "values": [1]}, {"name": "B", "values": [2]}],
            },
        }
    )
    series = result.metadata["chart_option"]["series"]
    assert all(s.get("stack") == "total" for s in series)


async def test_scatter_option(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {"block_id": "c1", "family": "scatter", "data": {"points": [[1, 2], [3, 4], [5, 6]]}}
    )
    opt = result.metadata["chart_option"]
    assert opt["xAxis"]["type"] == "value"
    assert opt["series"][0]["type"] == "scatter"
    assert opt["series"][0]["data"] == [[1, 2], [3, 4], [5, 6]]


async def test_multi_series_line(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "line",
            "data": {
                "labels": ["a", "b", "c"],
                "series": [{"name": "x", "values": [1, 2, 3]}, {"name": "y", "values": [4, 5, 6]}],
            },
        }
    )
    assert len(result.metadata["chart_option"]["series"]) == 2


async def test_title_and_axis_names(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "line",
            "data": {"labels": ["a", "b", "c"], "series": [{"name": "x", "values": [1, 2, 3]}]},
            "title": "GMV",
            "x_axis_name": "日期",
            "y_axis_name": "万元",
        }
    )
    opt = result.metadata["chart_option"]
    assert opt["title"]["text"] == "GMV"
    assert opt["xAxis"]["name"] == "日期"
    assert opt["yAxis"]["name"] == "万元"


# ----------------------------- 充分性 + fallback -----------------------------


async def test_line_insufficient_points_flags_fallback(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "line",
            "data": {"labels": ["a"][:MIN_TREND_POINTS], "series": [{"name": "x", "values": [1]}]},
        }
    )
    # labels 长度 < MIN_TREND_POINTS → 不足
    meta = result.metadata["chart_meta"]
    assert meta["data_sufficient"] is False
    assert meta["reason"] == "trend_needs_more_points"
    assert meta["fallback_family"] in ("grouped_bar", "kpi_card")


async def test_scatter_insufficient_observations_flags_fallback(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "scatter",
            "data": {"points": [[1, 2]] * (MIN_SCATTER_POINTS - 1)},
        }
    )
    meta = result.metadata["chart_meta"]
    assert meta["data_sufficient"] is False
    assert meta["reason"] == "scatter_needs_more_observations"
    assert meta["fallback_family"] == "table"


async def test_sufficient_no_fallback(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "line",
            "data": {
                "labels": ["a", "b", "c", "d"],
                "series": [{"name": "x", "values": [1, 2, 3, 4]}],
            },
        }
    )
    meta = result.metadata["chart_meta"]
    assert meta["data_sufficient"] is True
    assert meta["fallback_family"] is None
    assert meta["n_points"] == 4


# ----------------------------- artifact + 路径 -----------------------------


async def test_artifact_written_and_readable(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "bar",
            "data": {"labels": ["a"], "series": [{"name": "x", "values": [1]}]},
        }
    )
    out = Path(result.metadata["artifact_paths"][0])
    assert out.is_relative_to(tmp_path)
    assert out.name == "c1.json"
    assert out.exists()
    # JSON 可独立读回,shape 与 metadata.chart_option 一致
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded == result.metadata["chart_option"]


async def test_file_name_override(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "bar",
            "data": {"labels": ["a"], "series": [{"name": "x", "values": [1]}]},
            "file_name": "gmv.json",
        }
    )
    assert Path(result.metadata["artifact_paths"][0]).name == "gmv.json"


def test_path_containment_rejects_escape(tmp_path: Path):
    tool = _tool(tmp_path)
    # validate_input 先拒(bare-name);即便绕过,call 仍 is_relative_to 兜底
    assert (
        tool.validate_input(
            {
                "block_id": "c1",
                "family": "bar",
                "data": {"labels": ["a"], "series": [{"values": [1]}]},
                "file_name": "../evil.json",
            }
        ).valid
        is False
    )


async def test_call_time_is_relative_to_defense(tmp_path: Path):
    """直接 call()(绕过 validate)用逃逸 file_name → is_relative_to 兜底返 is_error。"""
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "bar",
            "data": {"labels": ["a"], "series": [{"name": "x", "values": [1]}]},
            "file_name": "../evil.json",
        }
    )
    assert result.is_error
    assert "escapes" in result.content or "Permission denied" in result.content


async def test_nan_data_rejected_cleanly(tmp_path: Path):
    """NaN/Infinity(pandas 常产)→ json.dumps allow_nan=False ValueError → 清晰 is_error(评审 High)。"""
    tool = _tool(tmp_path)
    result = await tool.call(
        {
            "block_id": "c1",
            "family": "bar",
            "data": {
                "labels": ["a", "b"],
                "series": [{"name": "x", "values": [1.0, float("nan")]}],
            },
        }
    )
    assert result.is_error
    assert "non-finite" in result.content or "NaN" in result.content


# ----------------------------- 与 html_report v2 组合 -----------------------------


async def test_chart_render_composes_with_html_report_v2(tmp_path: Path):
    """chart_render 产 option → html_report v2 charts map → 渲染 chart div + script。"""
    chart_tool = _tool(tmp_path)
    cr = await chart_tool.call(
        {
            "block_id": "gmv",
            "family": "bar",
            "data": {"labels": ["A", "B"], "series": [{"name": "GMV", "values": [7, 5]}]},
        }
    )
    from data_analysis_agent.reporting.contract import (
        BlockRole,
        ChartFamily,
        ChartSpec,
        ReportBlock,
        ReportContract,
        ReportDocument,
    )

    doc = ReportDocument(
        title="x",
        contract=ReportContract(question="q", explicit_requirement_refs=("u1",)),
        data_scope="s",
        blocks=(
            ReportBlock(
                block_id="gmv",
                role=BlockRole.CHART,
                chart=ChartSpec(family=ChartFamily.BAR, interpretation="A 领先"),
            ),
        ),
    )
    html_tool = HtmlReportTool(artifact_dir=tmp_path)
    page = html_tool._render_v2_page(doc.to_dict(), {"gmv": cr.metadata["chart_option"]})
    assert 'id="chart_gmv"' in page
    assert "render(" in page  # ECharts render call 注入
