"""NlQueryTool: Natural Language to Data Query (NL2SQL / NL2DataFrame).

Translates natural language queries into executable data queries.
Currently supports:
- CSV/Parquet files via pandas
- SQL databases via SQLAlchemy

Query intent is inferred via keyword heuristics; generated code is a
starting point that the model may refine with python_analysis.
"""

from __future__ import annotations

import re
from typing import Any

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class NlQueryTool(Tool):
    """Translate natural language into data queries."""

    # Keyword → pandas operation hints
    PANDAS_PATTERNS = [
        (r"\b(top|highest|largest|most)\s+(\d+)?", "nlargest"),
        (r"\b(bottom|lowest|smallest|least)\s+(\d+)?", "nsmallest"),
        (r"\b(average|mean|avg)\b", "mean"),
        (r"\b(sum|total)\b", "sum"),
        (r"\b(count|how many)\b", "count"),
        (r"\b(group by|grouped by)\b", "groupby"),
        (r"\b(sort|order)\s+by\b", "sort_values"),
        (r"\b(filter|where)\b", "filter"),
        (r"\b(describe|summary|overview)\b", "describe"),
        (r"\b(correlation|correlate)\b", "corr"),
        (r"\b(missing|null|na|nan)\b", "isnull"),
        (r"\b(unique|distinct)\b", "unique"),
    ]

    @property
    def name(self) -> str:
        return "nl_query"

    @property
    def description(self) -> str:
        return (
            "Convert natural language queries into data queries. "
            "Supports CSV/Parquet files and SQL databases. "
            "Example: 'Show me the top 10 products by revenue' → SQL or pandas code."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language data query",
                },
                "data_source": {
                    "type": "string",
                    "description": "Data source identifier (file path or DB connection string)",
                },
                "source_type": {
                    "type": "string",
                    "enum": ["csv", "parquet", "sql", "dataframe"],
                    "description": "Type of data source",
                },
            },
            "required": ["query", "data_source", "source_type"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        for field in ("query", "data_source", "source_type"):
            if not input_data.get(field):
                return ValidationResult.fail(f"{field} is required")
        if input_data.get("source_type") not in ("csv", "parquet", "sql", "dataframe"):
            return ValidationResult.fail("source_type must be one of: csv, parquet, sql, dataframe")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        """Generate query code based on source type and intent."""
        query = input_data["query"]
        data_source = input_data["data_source"]
        source_type = input_data["source_type"]

        if source_type in ("csv", "parquet"):
            code = self._generate_pandas(query, data_source, source_type)
        elif source_type == "sql":
            code = self._generate_sql(query, data_source)
        else:
            code = self._generate_dataframe(query, data_source)

        return ToolResult(
            content=(
                f"Generated query code for: '{query}'\n\n"
                f"Source: {data_source} ({source_type})\n\n"
                f"```python\n{code}\n```\n\n"
                "Use python_analysis tool to execute this code after refining the query logic."
            ),
            metadata={"generated_code": code, "source_type": source_type},
        )

    def _detect_intents(self, query: str) -> list[str]:
        """Detect query intents via keyword heuristics."""
        text = query.lower()
        intents = []
        for pattern, operation in self.PANDAS_PATTERNS:
            if re.search(pattern, text):
                intents.append(operation)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for i in intents:
            if i not in seen:
                seen.add(i)
                unique.append(i)
        return unique

    def _extract_number(self, query: str) -> int | None:
        """Try to extract a numeric limit (e.g. 'top 10')."""
        m = re.search(r"\b(\d+)\b", query)
        return int(m.group(1)) if m else None

    def _generate_pandas(self, query: str, data_source: str, source_type: str) -> str:
        """Generate pandas code for CSV/Parquet."""
        read_func = "pd.read_csv" if source_type == "csv" else "pd.read_parquet"
        intents = self._detect_intents(query)
        num = self._extract_number(query) or 10

        lines = [
            "import pandas as pd",
            f"df = {read_func}(r'{data_source}')",
            f"# User query: {query}",
            f"# Detected intents: {', '.join(intents) if intents else 'general exploration'}",
            "",
        ]

        if not intents:
            lines.append("result = df.head()")
            lines.append("print(result)")
            return "\n".join(lines)

        # Build a meaningful query chain
        if "isnull" in intents:
            lines.append("missing = df.isnull().sum().sort_values(ascending=False)")
            lines.append("print('Missing values per column:')")
            lines.append("print(missing[missing > 0])")
            return "\n".join(lines)

        if "describe" in intents:
            lines.append("result = df.describe(include='all')")
            lines.append("print(result)")
            return "\n".join(lines)

        if "corr" in intents:
            lines.append("numeric_df = df.select_dtypes(include='number')")
            lines.append("result = numeric_df.corr()")
            lines.append("print(result)")
            return "\n".join(lines)

        if "unique" in intents:
            lines.append("for col in df.select_dtypes(include='object').columns:")
            lines.append("    print(f'{col}: {df[col].nunique()} unique values')")
            return "\n".join(lines)

        # Aggregate / groupby / sort / filter logic
        lines.append("# Infer numeric columns for aggregation")
        lines.append("numeric_cols = df.select_dtypes(include='number').columns.tolist()")
        lines.append("cat_cols = df.select_dtypes(include='object').columns.tolist()")
        lines.append("")

        if "groupby" in intents and "mean" in intents:
            lines.append("if cat_cols and numeric_cols:")
            lines.append("    result = df.groupby(cat_cols[0])[numeric_cols].mean().reset_index()")
            lines.append("else:")
            lines.append("    result = df.mean(numeric_only=True)")
        elif "groupby" in intents and "sum" in intents:
            lines.append("if cat_cols and numeric_cols:")
            lines.append("    result = df.groupby(cat_cols[0])[numeric_cols].sum().reset_index()")
            lines.append("else:")
            lines.append("    result = df.sum(numeric_only=True)")
        elif "groupby" in intents and "count" in intents:
            lines.append("if cat_cols:")
            lines.append("    result = df.groupby(cat_cols[0]).size().reset_index(name='count')")
            lines.append("else:")
            lines.append("    result = df.count()")
        elif "mean" in intents:
            lines.append("result = df[numeric_cols].mean()")
        elif "sum" in intents:
            lines.append("result = df[numeric_cols].sum()")
        elif "count" in intents:
            lines.append("result = df.count()")
        elif "nlargest" in intents:
            lines.append("if numeric_cols:")
            lines.append(f"    result = df.nlargest({num}, numeric_cols[0])")
            lines.append("else:")
            lines.append("    result = df.head()")
        elif "nsmallest" in intents:
            lines.append("if numeric_cols:")
            lines.append(f"    result = df.nsmallest({num}, numeric_cols[0])")
            lines.append("else:")
            lines.append("    result = df.head()")
        elif "sort_values" in intents:
            lines.append("if numeric_cols:")
            lines.append(
                f"    result = df.sort_values(by=numeric_cols[0], ascending=False).head({num})"
            )
            lines.append("else:")
            lines.append("    result = df.head()")
        elif "filter" in intents:
            lines.append("# Attempt a generic filter on the first numeric column > median")
            lines.append("if numeric_cols:")
            lines.append("    median_val = df[numeric_cols[0]].median()")
            lines.append("    result = df[df[numeric_cols[0]] > median_val]")
            lines.append("else:")
            lines.append("    result = df.head()")
        else:
            lines.append("result = df.head()")

        lines.append("print(result)")
        return "\n".join(lines)

    def _generate_sql(self, query: str, data_source: str) -> str:
        """Generate SQL code based on query intent."""
        text = query.lower()
        intents = self._detect_intents(text)
        num = self._extract_number(text) or 10

        lines = [
            "import pandas as pd",
            "from sqlalchemy import create_engine",
            f"engine = create_engine(r'{data_source}')",
            f"# User query: {query}",
            f"# Detected intents: {', '.join(intents) if intents else 'general exploration'}",
            "",
            "# NOTE: Replace 'table_name' with the actual table name",
            "table_name = 'table_name'",
            "",
        ]

        # Build a basic SQL query from intent
        select_parts = ["SELECT *"]
        from_clause = "FROM {table_name}"
        where_clause = ""
        order_clause = ""
        limit_clause = f"LIMIT {num}"

        if "count" in intents:
            select_parts = ["SELECT COUNT(*)"]
            limit_clause = ""
        elif "mean" in intents or "sum" in intents:
            select_parts = ["SELECT *"]  # Model should refine to actual aggregate

        if "sort_values" in intents or "nlargest" in intents:
            order_clause = "ORDER BY column_name DESC"
        elif "nsmallest" in intents:
            order_clause = "ORDER BY column_name ASC"

        if "filter" in intents:
            where_clause = "WHERE column_name > value"

        sql_lines = [select_parts[0], from_clause]
        if where_clause:
            sql_lines.append(where_clause)
        if order_clause:
            sql_lines.append(order_clause)
        if limit_clause:
            sql_lines.append(limit_clause)

        lines.append('query_sql = """')
        lines.append("\n".join(sql_lines))
        lines.append('"""')
        lines.append("")
        lines.append("result = pd.read_sql(query_sql, engine)")
        lines.append("print(result)")

        return "\n".join(lines)

    def _generate_dataframe(self, query: str, data_source: str) -> str:
        """Generate code for an in-memory DataFrame source."""
        intents = self._detect_intents(query)
        num = self._extract_number(query) or 10

        lines = [
            f"# DataFrame source: {data_source}",
            f"# User query: {query}",
            f"# Detected intents: {', '.join(intents) if intents else 'general exploration'}",
            "# The dataframe is already available as 'df'",
            "",
        ]

        if not intents:
            lines.append("result = df.head()")
        elif "describe" in intents:
            lines.append("result = df.describe(include='all')")
        elif "isnull" in intents:
            lines.append("result = df.isnull().sum().sort_values(ascending=False)")
        elif "corr" in intents:
            lines.append("result = df.select_dtypes(include='number').corr()")
        elif "nlargest" in intents:
            lines.append("numeric_cols = df.select_dtypes(include='number').columns.tolist()")
            lines.append("if numeric_cols:")
            lines.append(f"    result = df.nlargest({num}, numeric_cols[0])")
            lines.append("else:")
            lines.append("    result = df.head()")
        elif "nsmallest" in intents:
            lines.append("numeric_cols = df.select_dtypes(include='number').columns.tolist()")
            lines.append("if numeric_cols:")
            lines.append(f"    result = df.nsmallest({num}, numeric_cols[0])")
            lines.append("else:")
            lines.append("    result = df.head()")
        elif "mean" in intents:
            lines.append("result = df.select_dtypes(include='number').mean()")
        elif "sum" in intents:
            lines.append("result = df.select_dtypes(include='number').sum()")
        elif "count" in intents:
            lines.append("result = df.count()")
        elif "unique" in intents:
            lines.append("for col in df.select_dtypes(include='object').columns:")
            lines.append("    print(f'{col}: {df[col].nunique()} unique values')")
            lines.append("result = None")
        else:
            lines.append("result = df.head()")

        lines.append("if result is not None:")
        lines.append("    print(result)")

        return "\n".join(lines)
