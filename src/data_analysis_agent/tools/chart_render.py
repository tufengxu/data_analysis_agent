"""ChartRenderTool: 结构化 ChartSpec + 数据 → ECharts option + artifact(写产物)。

消费结构化字段(图族 + 数据)生成 ECharts 5 option dict(浏览器侧渲染,本工具不渲染像素),
做数据充分性检查(``reporting.chart_rules``),把 option 写成 ``<block_id>.json`` 落盘,
返回 option + chart metadata。取代"模型手写 ECharts option"的默认报告图表路径(spec §5.2)。

设计要点:
- 按图族生成 option(line/bar/grouped_bar/stacked_bar/scatter;其余图族延后)。
- 充分性不足时**仍生成 option**(不阻断),metadata 标 ``data_sufficient=False`` + ``reason``
  + ``fallback_family``(``suggest_fallback``);阻断留给 ``run_qa``。
- ``chart_meta.n_points``/``n_observations``/``data_sufficient`` 与 ``run_qa`` 的
  ``n_points_by_chart``/``n_observations_by_chart`` 及 ``ChartSpec.data_sufficient`` 对齐。
- 路径防护:``block_id``/``file_name`` bare-name + ``is_relative_to(artifact_dir)`` 重检。
- 非只读(写产物);``is_concurrency_safe=False`` 与 html_report 一致(同 block_id 并发竞态)。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from data_analysis_agent.reporting.chart_rules import (
    check_data_sufficiency,
    suggest_fallback,
)
from data_analysis_agent.reporting.contract import ChartFamily

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

_SUPPORTED_FAMILIES = (
    "line",
    "bar",
    "grouped_bar",
    "stacked_bar",
    "scatter",
    "heatmap",
    "funnel",
)

# Windows 设备名(镜像 html_report 的 _WINDOWS_RESERVED_NAMES;复制以避免动 html_report)。
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class ChartRenderTool(Tool):
    """Render a structured chart spec + data into an ECharts option + JSON artifact."""

    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        if artifact_dir is None:
            artifact_dir = Path(tempfile.mkdtemp(prefix="daa_charts_"))
        self.artifact_dir = Path(artifact_dir).expanduser().resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "chart_render"

    @property
    def description(self) -> str:
        return (
            "Render a structured chart request into an ECharts option + JSON artifact, "
            "WITHOUT writing free-form Python. Pass family (line/bar/grouped_bar/"
            "stacked_bar/scatter/heatmap/funnel) + data ({labels, series:[{name, values}]} "
            "for line/bar; {points:[[x,y],...]} for scatter; "
            "{x_labels, y_labels, values:[[x,y,val],...]} for heatmap; "
            "{stages:[{name, value}]} for funnel). Returns the chart_option "
            "(feed it into html_report's charts map under the same block_id) + chart metadata "
            "(family, data_sufficient, n_points, fallback_family). Read the data with "
            "python_analysis first so numbers are kernel-exact."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "block_id": {
                    "type": "string",
                    "description": "Block id linking this chart to a html_report v2 CHART block.",
                },
                "family": {"type": "string", "enum": list(_SUPPORTED_FAMILIES)},
                "data": {
                    "type": "object",
                    "description": (
                        "{labels:[...], series:[{name, values:[...]}]} for line/bar/grouped_bar/"
                        "stacked_bar; {points:[[x,y],...]} for scatter."
                    ),
                },
                "title": {"type": "string"},
                "x_axis_name": {"type": "string"},
                "y_axis_name": {"type": "string"},
                "file_name": {
                    "type": "string",
                    "description": "Optional bare file name (no dirs).",
                },
            },
            "required": ["block_id", "family", "data"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return False  # 同 block_id 并发写同文件竞态(与 html_report 一致,保守)

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return False  # 写 artifact

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    @staticmethod
    def _validate_bare_name(name: str) -> str | None:
        # 镜像 html_report 的 bare-name 规则(复制以避免改 html_report):
        # NUL / 目录或点开头 / 点·空格结尾 / Windows 保留名。
        if "\x00" in name:
            return "must not contain NUL characters"
        if Path(name).name != name or name.startswith("."):
            return "must be a bare file name (no directories)"
        if name.endswith((".", " ")):
            return "must not end with a dot or space"
        stem = name.split(".", 1)[0].strip().upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            return f"'{name}' is a reserved device name"
        return None

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        block_id = input_data.get("block_id")
        if not isinstance(block_id, str) or not block_id.strip():
            return ValidationResult.fail("block_id is required and must be a non-empty string")
        err = self._validate_bare_name(block_id)
        if err:
            return ValidationResult.fail(f"block_id {err}")
        family = input_data.get("family")
        if family not in _SUPPORTED_FAMILIES:
            return ValidationResult.fail(f"family must be one of {list(_SUPPORTED_FAMILIES)}")
        data = input_data.get("data")
        if not isinstance(data, dict):
            return ValidationResult.fail("data must be an object")
        if family == "scatter":
            points = data.get("points")
            if not isinstance(points, list):
                return ValidationResult.fail("scatter requires data.points as an array of [x, y]")
            for i, p in enumerate(points):
                if not isinstance(p, (list, tuple)) or len(p) != 2:
                    return ValidationResult.fail(f"data.points[{i}] must be a [x, y] pair")
        elif family == "heatmap":
            x_labels = data.get("x_labels")
            if not isinstance(x_labels, list) or not x_labels:
                return ValidationResult.fail("heatmap requires a non-empty data.x_labels array")
            y_labels = data.get("y_labels")
            if not isinstance(y_labels, list) or not y_labels:
                return ValidationResult.fail("heatmap requires a non-empty data.y_labels array")
            values = data.get("values")
            if not isinstance(values, list) or not values:
                return ValidationResult.fail(
                    "heatmap requires a non-empty data.values array of [x, y, value]"
                )
            for i, v in enumerate(values):
                if not isinstance(v, (list, tuple)) or len(v) != 3:
                    return ValidationResult.fail(f"data.values[{i}] must be a [x, y, value] triple")
        elif family == "funnel":
            stages = data.get("stages")
            if not isinstance(stages, list) or not stages:
                return ValidationResult.fail(
                    "funnel requires a non-empty data.stages array of {name, value}"
                )
            for i, s in enumerate(stages):
                if not isinstance(s, dict) or "name" not in s or "value" not in s:
                    return ValidationResult.fail(f"data.stages[{i}] must have name and value")
        else:
            labels = data.get("labels")
            if not isinstance(labels, list) or not labels:
                return ValidationResult.fail(f"{family} requires a non-empty data.labels array")
            series = data.get("series")
            if not isinstance(series, list) or not series:
                return ValidationResult.fail(f"{family} requires a non-empty data.series array")
            for idx, s in enumerate(series):
                if not isinstance(s, dict) or not isinstance(s.get("values"), list):
                    return ValidationResult.fail(f"data.series[{idx}].values is required (array)")
                if len(s["values"]) != len(labels):
                    return ValidationResult.fail(
                        f"data.series[{idx}].values length ({len(s['values'])}) "
                        f"!= labels length ({len(labels)})"
                    )
        file_name = input_data.get("file_name")
        if file_name is not None:
            if not isinstance(file_name, str) or not file_name.strip():
                return ValidationResult.fail("file_name must be a non-empty string")
            if not file_name.endswith(".json"):
                return ValidationResult.fail("file_name must end with .json")
            err = self._validate_bare_name(file_name)
            if err:
                return ValidationResult.fail(f"file_name {err}")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        block_id = input_data["block_id"]
        family = input_data["family"]
        data = input_data["data"]
        cf = ChartFamily(family)

        option = self._build_option(cf, data, input_data)

        n_points: int | None
        n_observations: int | None
        if cf is ChartFamily.SCATTER:
            n_points = None
            n_observations = len(data["points"])
        elif cf is ChartFamily.HEATMAP:
            n_points = len(data["values"])
            n_observations = None
        elif cf is ChartFamily.FUNNEL:
            n_points = len(data["stages"])
            n_observations = None
        else:
            n_points = len(data["labels"])
            n_observations = None
        sufficient, reason = check_data_sufficiency(
            cf, n_points=n_points, n_observations=n_observations
        )
        fallback: str | None = None
        if not sufficient:
            fb = suggest_fallback(cf, n_points=n_points, n_observations=n_observations)
            fallback = fb.value if fb is not None else None

        file_name = input_data.get("file_name") or f"{block_id}.json"
        out_path = (self.artifact_dir / file_name).resolve()
        if not out_path.is_relative_to(self.artifact_dir):
            return ToolResult(
                content="Permission denied: chart path escapes the artifact directory.",
                is_error=True,
            )
        try:
            payload = json.dumps(option, ensure_ascii=False, allow_nan=False)
        except ValueError:
            # NaN/Infinity(pandas 常产)非 JSON 合规;给清晰错误而非崩(评审 High)
            return ToolResult(
                content="chart data contains non-finite float values (NaN/Infinity); "
                "clean or impute them before rendering",
                is_error=True,
            )
        try:
            out_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            return ToolResult(content=f"Failed to write chart artifact: {exc}", is_error=True)

        meta = {
            "family": family,
            "block_id": block_id,
            "data_sufficient": sufficient,
            "reason": reason,
            "fallback_family": fallback,
            "n_points": n_points,
            "n_observations": n_observations,
        }
        return ToolResult(
            content=_summarize(family, sufficient, fallback, out_path),
            metadata={
                "chart_option": option,
                "artifact_paths": [str(out_path)],
                "chart_meta": meta,
            },
        )

    @staticmethod
    def _build_option(
        cf: ChartFamily, data: dict[str, Any], input_data: dict[str, Any]
    ) -> dict[str, Any]:
        x_name = data.get("x_axis_name") or input_data.get("x_axis_name")
        y_name = data.get("y_axis_name") or input_data.get("y_axis_name")
        title = input_data.get("title")
        option: dict[str, Any] = {}
        if isinstance(title, str) and title:
            option["title"] = {"text": title}
        if cf is ChartFamily.SCATTER:
            x_axis: dict[str, Any] = {"type": "value"}
            if x_name:
                x_axis["name"] = x_name
            y_axis = {"type": "value"}
            if y_name:
                y_axis["name"] = y_name
            option["xAxis"] = x_axis
            option["yAxis"] = y_axis
            option["series"] = [{"type": "scatter", "data": data["points"]}]
            return option
        if cf is ChartFamily.HEATMAP:
            option["tooltip"] = {"position": "top"}
            option["xAxis"] = {"type": "category", "data": data["x_labels"]}
            option["yAxis"] = {"type": "category", "data": data["y_labels"]}
            vals = [v[2] for v in data["values"] if isinstance(v[2], (int, float))]
            option["visualMap"] = {
                "min": min(vals) if vals else 0,
                "max": max(vals) if vals else 1,
                "calculable": True,
                "orient": "horizontal",
                "left": "center",
                "bottom": 0,
            }
            option["series"] = [{"type": "heatmap", "data": data["values"]}]
            return option
        if cf is ChartFamily.FUNNEL:
            option["tooltip"] = {"trigger": "item", "formatter": "{b}: {c}"}
            option["series"] = [{"type": "funnel", "data": data["stages"]}]
            return option
        # line / bar / grouped_bar / stacked_bar —— 共享 category xAxis
        x_axis = {"type": "category", "data": data["labels"]}
        if x_name:
            x_axis["name"] = x_name
        y_axis = {"type": "value"}
        if y_name:
            y_axis["name"] = y_name
        option["xAxis"] = x_axis
        option["yAxis"] = y_axis
        series_type = "line" if cf is ChartFamily.LINE else "bar"
        series = []
        for s in data["series"]:
            item: dict[str, Any] = {
                "name": s.get("name", ""),
                "type": series_type,
                "data": s["values"],
            }
            if cf is ChartFamily.STACKED_BAR:
                item["stack"] = "total"
            series.append(item)
        option["series"] = series
        return option


def _summarize(family: str, sufficient: bool, fallback: str | None, out_path: Path) -> str:
    head = f"chart_render({family}) → {out_path.name}"
    if sufficient:
        return f"{head};数据充分。"
    fb = f";建议 fallback 图族:{fallback}" if fallback else ""
    return f"{head};数据充分性不足{fb}。"
