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
            "1. Run report_need FIRST to parse the request into EXPLICIT vs IMPLICIT "
            "requirements; mark inferred requirements as inferred and carry that "
            "mapping into report_contract — never treat inferences as explicit facts\n"
            "2. Run data_profile, then report_context to capture Data Context + Process "
            "Context (candidate columns, business grain, tool steps, assumptions)\n"
            "3. Run report_contract to canonicalize the Report Contract BEFORE heavy "
            "analysis; it populates traceability refs + field_sources and surfaces "
            "missing_context (clarify or assume before rendering)\n"
            "4. Run the analysis: compute every statistic, aggregate and series with "
            "python_analysis (kernel state persists across calls — reuse variables "
            "instead of reloading); print chart-ready data as compact JSON so you can "
            "copy exact numbers into chart options\n"
            "5. Call html_report ONCE with an executive summary plus one section per "
            "finding; each chart is a full ECharts `option` object (set textStyle, axis "
            "names and series names in the user's language); keep tables small (top-N "
            "rows); tell the user the report file path returned by the tool\n"
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
            "日报",
            "周报",
            "月报",
            "复盘",
            "漏斗",
            "同期群",
            "留存",
            "异常",
            "风险",
            "数据质量",
            "KPI",
            "daily",
            "weekly",
            "funnel",
            "cohort",
            "risk",
            "anomaly",
            "data quality",
            "diagnostic",
        ]

    @property
    def allowed_tools(self) -> list[str]:
        return [
            "read_file",
            "data_profile",
            "report_need",
            "report_context",
            "report_contract",
            "python_analysis",
            "retrieve_result",
            "visualization",
            "html_report",
        ]

    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        return SkillResult(
            output=f"Report generation skill activated for: {query}",
            tools_used=["read_file", "python_analysis", "html_report"],
        )


class JointAnalysisSkill(Skill):
    """Skill for multi-sheet / multi-file joint analysis (discover → join → analyse)."""

    @property
    def name(self) -> str:
        return "joint_analysis"

    @property
    def description(self) -> str:
        return (
            "Analyse data spread across multiple sheets or multiple files: discover "
            "each table's structure, join/merge them on shared keys, then analyse the "
            "combined dataset. Use for Excel workbooks with several sheets or a folder "
            "of related CSV/Excel files."
        )

    @property
    def instructions(self) -> str:
        return (
            "When joining data across sheets or files:\n"
            "1. Run data_profile on each file (or the directory) FIRST to list every "
            "sheet, its columns and dtypes; note the absolute paths it reports\n"
            "2. Load each table once with python_analysis (pd.read_csv / "
            "pd.read_excel(path, sheet_name=...)) using those absolute paths; the "
            "kernel keeps DataFrames across calls, so do not reload\n"
            "3. Identify the join key(s) by comparing column names and value overlap; "
            "decide the join type (inner/left/outer) and granularity deliberately\n"
            "4. Merge with pd.merge / pd.concat, then VALIDATE the join: compare row "
            "counts before vs after, check for unmatched keys (how='left' + indicator) "
            "and unintended row multiplication from many-to-many keys\n"
            "5. Only after the combined table is verified, run the requested analysis "
            "and report which keys were joined and how many rows matched\n"
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "join",
            "merge",
            "multi-file",
            "multi-sheet",
            "联合分析",
            "多表",
            "多文件",
            "多sheet",
            "关联",
            "合并",
            "vlookup",
        ]

    @property
    def allowed_tools(self) -> list[str]:
        return [
            "read_file",
            "data_profile",
            "python_analysis",
            "retrieve_result",
            "visualization",
            "html_report",
        ]

    async def execute(self, query: str, context: dict[str, Any]) -> SkillResult:
        return SkillResult(
            output=f"Joint analysis skill activated for: {query}",
            tools_used=["data_profile", "python_analysis"],
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
