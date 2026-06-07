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
