"""VisualizationTool: generate charts and reports from analysis results.

Outputs: PNG, SVG, HTML via matplotlib, seaborn, plotly.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class VisualizationTool(Tool):
    """Generate visualizations from data or analysis results."""

    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        # Default save location for generated chart code. Relative paths land
        # in the execution sandbox and may be destroyed with it; an absolute
        # artifact dir makes charts actually reachable by the user.
        self.artifact_dir = Path(artifact_dir).expanduser().resolve() if artifact_dir else None

    @property
    def name(self) -> str:
        return "visualization"

    @property
    def description(self) -> str:
        return (
            "Generate data visualizations and charts. "
            "Supports matplotlib, seaborn, and plotly. "
            "Outputs PNG, SVG, or HTML. "
            "Use this after data analysis to create charts, plots, and dashboards."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": [
                        "line",
                        "bar",
                        "scatter",
                        "histogram",
                        "box",
                        "heatmap",
                        "pie",
                        "area",
                        "pair",
                    ],
                    "description": "Type of chart to generate",
                },
                "data_source": {
                    "type": "string",
                    "description": "Path to data file or variable reference",
                },
                "x_column": {
                    "type": "string",
                    "description": "Column name for X axis",
                },
                "y_column": {
                    "type": "string",
                    "description": "Column name for Y axis",
                },
                "title": {
                    "type": "string",
                    "description": "Chart title",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["png", "svg", "html"],
                    "description": "Output format (default: png)",
                },
                "output_path": {
                    "type": "string",
                    "description": "Path to save the visualization (optional)",
                },
                "code": {
                    "type": "string",
                    "description": "Custom Python visualization code (optional, overrides other params)",
                },
            },
            "required": ["chart_type"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return False

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        chart_type = input_data.get("chart_type")
        if not chart_type:
            return ValidationResult.fail("chart_type is required")
        valid_types = [
            "line",
            "bar",
            "scatter",
            "histogram",
            "box",
            "heatmap",
            "pie",
            "area",
            "pair",
        ]
        if chart_type not in valid_types:
            return ValidationResult.fail(f"chart_type must be one of: {valid_types}")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        chart_type = input_data["chart_type"]
        data_source = input_data.get("data_source", "")
        x_col = input_data.get("x_column", "")
        y_col = input_data.get("y_column", "")
        title = input_data.get("title", "Chart")
        output_format = input_data.get("output_format", "png")
        output_path = input_data.get("output_path", "")
        custom_code = input_data.get("code", "")

        if custom_code:
            code = custom_code
        else:
            code = self._generate_code(
                chart_type, data_source, x_col, y_col, title, output_format, output_path
            )

        return ToolResult(
            content=(
                f"Generated {chart_type} chart code:\n\n```python\n{code}\n```\n\n"
                f"Use python_analysis tool to execute this code."
            ),
            metadata={
                "chart_type": chart_type,
                "output_format": output_format,
                "generated_code": code,
            },
        )

    def _generate_code(
        self,
        chart_type: str,
        data_source: str,
        x_col: str,
        y_col: str,
        title: str,
        output_format: str,
        output_path: str,
    ) -> str:
        """Generate matplotlib/seaborn/plotly code for the requested chart."""
        save_path = output_path or self._default_save_path(output_format)

        if output_format == "html":
            return self._generate_plotly_code(
                chart_type, data_source, x_col, y_col, title, save_path
            )
        return self._generate_matplotlib_code(
            chart_type, data_source, x_col, y_col, title, save_path, output_format
        )

    def _default_save_path(self, output_format: str) -> str:
        name = f"chart_{uuid.uuid4().hex[:8]}.{output_format}"
        if self.artifact_dir is not None:
            return str(self.artifact_dir / name)
        return name

    def _generate_matplotlib_code(
        self,
        chart_type: str,
        data_source: str,
        x_col: str,
        y_col: str,
        title: str,
        save_path: str,
        fmt: str,
    ) -> str:
        code_lines = [
            "import pandas as pd",
            "import matplotlib.pyplot as plt",
            "import seaborn as sns",
            "",
            f"df = pd.read_csv('{data_source}')"
            if data_source
            else "# df should be defined in context",
            "",
            "plt.figure(figsize=(10, 6))",
        ]

        if chart_type == "line":
            code_lines.append(f"sns.lineplot(data=df, x='{x_col}', y='{y_col}')")
        elif chart_type == "bar":
            code_lines.append(f"sns.barplot(data=df, x='{x_col}', y='{y_col}')")
        elif chart_type == "scatter":
            code_lines.append(f"sns.scatterplot(data=df, x='{x_col}', y='{y_col}')")
        elif chart_type == "histogram":
            col = x_col or y_col or "df.columns[0]"
            code_lines.append(f"sns.histplot(data=df, x='{col}', kde=True)")
        elif chart_type == "box":
            code_lines.append(f"sns.boxplot(data=df, x='{x_col}', y='{y_col}')")
        elif chart_type == "heatmap":
            code_lines.append("corr = df.corr(numeric_only=True)")
            code_lines.append("sns.heatmap(corr, annot=True, cmap='coolwarm', fmt='.2f')")
        elif chart_type == "pie":
            code_lines.append(f"df['{x_col}'].value_counts().plot.pie(autopct='%1.1f%%')")
        elif chart_type == "area":
            code_lines.append(f"df.plot.area(x='{x_col}', y='{y_col}')")
        elif chart_type == "pair":
            code_lines.append("sns.pairplot(df.select_dtypes(include='number').dropna())")

        code_lines.extend(
            [
                f"plt.title('{title}')",
                "plt.tight_layout()",
                f"plt.savefig('{save_path}', format='{fmt}', dpi=150)",
                f"print('Chart saved to: {save_path}')",
                "plt.close()",
            ]
        )
        code_lines.extend(self._emit_artifact_lines(save_path, fmt))

        return "\n".join(code_lines)

    @staticmethod
    def _emit_artifact_lines(save_path: str, fmt: str) -> list[str]:
        """Report the saved chart to the sandbox so it reaches ArtifactStore.

        ``agent_result`` only exists inside the python_analysis sandbox/kernel;
        the guard keeps the generated code runnable elsewhere.
        """
        return [
            "try:",
            f"    agent_result([{{'type': 'image', 'path': r'{save_path}', 'format': '{fmt}'}}])",
            "except NameError:",
            "    pass",
        ]

    def _generate_plotly_code(
        self,
        chart_type: str,
        data_source: str,
        x_col: str,
        y_col: str,
        title: str,
        save_path: str,
    ) -> str:
        chart_func = {
            "line": "px.line",
            "bar": "px.bar",
            "scatter": "px.scatter",
            "histogram": "px.histogram",
            "box": "px.box",
            "area": "px.area",
            "pie": "px.pie",
        }.get(chart_type, "px.scatter")

        code_lines = [
            "import pandas as pd",
            "import plotly.express as px",
            "",
            f"df = pd.read_csv('{data_source}')"
            if data_source
            else "# df should be defined in context",
            "",
        ]

        if chart_type == "pie":
            code_lines.append(
                f"fig = {chart_func}(df, names='{x_col}', values='{y_col}', title='{title}')"
            )
        elif chart_type == "heatmap":
            code_lines.extend(
                [
                    "import plotly.graph_objects as go",
                    "corr = df.corr(numeric_only=True)",
                    "fig = go.Figure(data=go.Heatmap(z=corr.values, x=corr.columns, y=corr.columns, colorscale='RdBu'))",
                    f"fig.update_layout(title='{title}')",
                ]
            )
        else:
            code_lines.append(f"fig = {chart_func}(df, x='{x_col}', y='{y_col}', title='{title}')")

        code_lines.extend(
            [
                f"fig.write_html('{save_path}')",
                f"print('Interactive chart saved to: {save_path}')",
            ]
        )
        code_lines.extend(self._emit_artifact_lines(save_path, "html"))

        return "\n".join(code_lines)
