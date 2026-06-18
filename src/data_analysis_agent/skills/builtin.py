"""Built-in skills for common data analysis tasks."""

from __future__ import annotations

from typing import Any

from .base import Skill, SkillResult


class DescriptiveAnalysisSkill(Skill):
    """Skill for descriptive statistical analysis."""

    @property
    def name(self) -> str:
        return "descriptive_analysis"

    @property
    def description(self) -> str:
        return (
            "Perform descriptive statistical analysis on datasets. "
            "Includes mean, median, std, percentiles, distributions, and summary tables."
        )

    @property
    def instructions(self) -> str:
        return (
            "When performing descriptive analysis:\n"
            "1. Load the data and inspect its structure\n"
            "2. Compute basic statistics (count, mean, std, min, max, quartiles)\n"
            "3. Identify missing values and data types\n"
            "4. Generate a concise summary report\n"
            "5. If appropriate, create visualizations (histograms, box plots)\n"
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "descriptive",
            "summary statistics",
            "describe",
            "distribution",
            "描述性统计",
            "统计描述",
            "数据概览",
            "分布",
        ]

    @property
    def allowed_tools(self) -> list[str]:
        return ["read_file", "python_analysis"]

    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        return SkillResult(
            output=f"Descriptive analysis skill activated for: {query}",
            tools_used=["read_file", "python_analysis"],
        )


class CorrelationAnalysisSkill(Skill):
    """Skill for correlation and relationship analysis."""

    @property
    def name(self) -> str:
        return "correlation_analysis"

    @property
    def description(self) -> str:
        return (
            "Analyze correlations and relationships between variables. "
            "Supports Pearson, Spearman, mutual information, and heatmap visualizations."
        )

    @property
    def instructions(self) -> str:
        return (
            "When performing correlation analysis:\n"
            "1. Select numeric columns for correlation computation\n"
            "2. Compute Pearson and Spearman correlation matrices\n"
            "3. Identify strongly correlated pairs (|r| > 0.7)\n"
            "4. Generate a correlation heatmap\n"
            "5. Highlight potential multicollinearity issues\n"
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "correlation",
            "correlate",
            "relationship",
            "pearson",
            "spearman",
            "相关",
            "相关性",
            "关系分析",
        ]

    @property
    def allowed_tools(self) -> list[str]:
        return ["read_file", "python_analysis"]

    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        return SkillResult(
            output=f"Correlation analysis skill activated for: {query}",
            tools_used=["read_file", "python_analysis"],
        )


class ReportGenerationSkill(Skill):
    """Skill for producing an H5 HTML report with ECharts visualizations."""

    @property
    def name(self) -> str:
        return "report_generation"

    @property
    def description(self) -> str:
        return (
            "Produce a self-contained H5 HTML analysis report with ECharts charts. "
            "Covers executive summary, sectioned findings, charts and data tables."
        )

    @property
    def instructions(self) -> str:
        return (
            "When generating an HTML analysis report:\n"
            "1. Run the analysis first: load data and compute every statistic, "
            "aggregate and series with python_analysis (kernel state persists "
            "across calls — reuse variables instead of reloading)\n"
            "2. Print chart-ready data as compact JSON (e.g. lists of category "
            "labels and values) so you can copy exact numbers into chart options\n"
            "3. Design the report: an executive summary plus one section per "
            "finding; every key claim should be backed by a chart or table\n"
            "4. Call html_report ONCE with all sections. Each chart is a full "
            "ECharts `option` object (set textStyle, axis names and series names "
            "in the user's language); keep tables small (top-N rows)\n"
            "5. Tell the user the report file path returned by the tool\n"
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "report",
            "html report",
            "dashboard",
            "echarts",
            "报告",
            "分析报告",
            "可视化报告",
            "汇报",
            "h5",
        ]

    @property
    def allowed_tools(self) -> list[str]:
        return ["read_file", "python_analysis", "retrieve_result", "html_report"]

    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        return SkillResult(
            output=f"Report generation skill activated for: {query}",
            tools_used=["read_file", "python_analysis", "html_report"],
        )


class TrendAnalysisSkill(Skill):
    """Skill for time-series trend analysis."""

    @property
    def name(self) -> str:
        return "trend_analysis"

    @property
    def description(self) -> str:
        return (
            "Analyze trends in time-series data. "
            "Includes trend decomposition, seasonality detection, and forecasting."
        )

    @property
    def instructions(self) -> str:
        return (
            "When performing trend analysis:\n"
            "1. Parse date/time columns and set as index\n"
            "2. Resample data to appropriate granularity\n"
            "3. Decompose trend, seasonality, and residual components\n"
            "4. Compute rolling averages and growth rates\n"
            "5. Generate time-series plots with trend lines\n"
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "trend",
            "time series",
            "seasonality",
            "forecast",
            "趋势",
            "时间序列",
            "季节性",
            "预测",
        ]

    @property
    def allowed_tools(self) -> list[str]:
        return ["read_file", "python_analysis"]

    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        return SkillResult(
            output=f"Trend analysis skill activated for: {query}",
            tools_used=["read_file", "python_analysis"],
        )
