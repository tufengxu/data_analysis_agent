"""NlQueryTool: Natural Language to Data Query (NL2SQL / NL2DataFrame).

Translates natural language queries into executable data queries.
Currently supports:
- CSV/Parquet files via pandas
- SQL databases via SQLAlchemy

Query intent is inferred via keyword heuristics; generated code is a STARTING
POINT that the model refines with python_analysis (assistive, not authoritative).

P1-4.6:
- schema-aware: pass an optional ``schema`` (from data_profile) so generated
  code references REAL column names instead of ``numeric_cols[0]`` placeholders.
- secret guard: SQL connection strings with embedded credentials (``user:pass@``)
  are NOT inlined into the generated code — it reads from ``$DB_URL`` and the
  displayed/trajectorized source is redacted.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import ParseResult, urlparse, urlunparse

from .base import CanUseToolFn, Tool, ToolResult, ValidationResult

# A dtype is treated as NON-numeric (categorical/text/temporal) if it contains
# any of these tokens; everything else (int/float/double/real/number/numeric/
# decimal) is numeric. A deny-list is more robust to new numeric spellings than
# an allow-list (e.g. pyarrow stringifies float64 as "double", SQL uses "numeric").
_CATEGORICAL_DTYPE_TOKENS = (
    "object",
    "str",
    "string",
    "char",
    "text",
    "bool",
    "boolean",
    "date",
    "time",
    "period",
    "category",
    "interval",
    "json",
    "geometry",
    "point",
    "bytes",
    "void",
)
# Minimum column-name length to match against the query as a substring, so a
# 1-2 char column ("a", "id") doesn't fire inside unrelated words ("data",
# "did"). Shorter columns fall back to order, not substring.
_MIN_COLNAME_MATCH_LEN = 3


class NlQueryTool(Tool):
    """Translate natural language into data queries (assistive, read-only)."""

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
            "Convert a natural-language data query into pandas/SQL CODE (a heuristic "
            "DRAFT — refine with python_analysis; assistive, not authoritative). "
            "Supports CSV/Parquet files and SQL databases. Optionally pass `schema` "
            "(data_profile's column list) so the generated code uses real column "
            "names. SQL connection strings with embedded credentials are read via "
            "$DB_URL, never inlined."
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
                "schema": {
                    "type": "array",
                    "description": (
                        "Optional column list from data_profile "
                        "([{name, dtype}, ...]) — makes the generated code "
                        "schema-aware (real column names)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "dtype": {"type": "string"},
                        },
                    },
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
        numeric_cols, cat_cols = self._columns_from_schema(input_data.get("schema"))

        warnings: list[str] = []
        display_source = data_source
        if source_type in ("csv", "parquet"):
            code = self._generate_pandas(query, data_source, source_type, numeric_cols, cat_cols)
        elif source_type == "sql":
            if self._has_embedded_credentials(data_source):
                warnings.append(
                    "SQL connection string had embedded credentials; generated code "
                    "reads $DB_URL instead — set it in the environment, never hardcode."
                )
                display_source = self._redact_connection_string(data_source)
            code = self._generate_sql(query, data_source)
        else:
            code = self._generate_dataframe(query, data_source, numeric_cols, cat_cols)

        content = (
            f"Generated query code for: '{query}'\n\n"
            f"Source: {display_source} ({source_type})\n\n"
            f"```python\n{code}\n```\n\n"
            "Use python_analysis tool to execute this code after refining the query logic."
        )
        for w in warnings:
            content += f"\n⚠ {w}"
        return ToolResult(
            content=content,
            metadata={"generated_code": code, "source_type": source_type, "warnings": warnings},
        )

    # --- helpers ---------------------------------------------------------------

    def _detect_intents(self, query: str) -> list[str]:
        """Detect query intents via keyword heuristics."""
        text = query.lower()
        intents = []
        for pattern, operation in self.PANDAS_PATTERNS:
            if re.search(pattern, text):
                intents.append(operation)
        seen: set[str] = set()
        unique: list[str] = []
        for i in intents:
            if i not in seen:
                seen.add(i)
                unique.append(i)
        return unique

    def _extract_number(self, query: str) -> int | None:
        """Try to extract a numeric limit (e.g. 'top 10')."""
        m = re.search(r"\b(\d+)\b", query)
        return int(m.group(1)) if m else None

    def _columns_from_schema(self, schema: Any) -> tuple[list[str] | None, list[str] | None]:
        """Resolve an optional schema input into (numeric_names, categorical_names).

        Accepts either a list of {name, dtype} or a dict with a ``columns`` key
        (the data_profile table shape). Returns (None, None) when no schema was
        supplied so generators fall back to the generic runtime-detection path.
        """
        if not schema:
            return None, None
        cols = schema.get("columns") if isinstance(schema, dict) else schema
        if not isinstance(cols, list):
            return None, None
        numeric: list[str] = []
        categorical: list[str] = []
        for c in cols:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            dtype = str(c.get("dtype", "")).lower()
            if any(tok in dtype for tok in _CATEGORICAL_DTYPE_TOKENS):
                categorical.append(name)
            else:
                numeric.append(name)
        return numeric, categorical

    def _pick_numeric_column(self, query: str, numeric_cols: list[str]) -> str | None:
        """Pick the numeric column most relevant to the query.

        A column name ≥3 chars that appears in the query wins; shorter names are
        not substring-matched (they fire inside unrelated words — "a" in "data").
        Falls back to the first numeric column.
        """
        if not numeric_cols:
            return None
        lowered = query.lower()
        for col in numeric_cols:
            token = col.lower()
            if len(token) >= _MIN_COLNAME_MATCH_LEN and token in lowered:
                return col
        return numeric_cols[0]

    def _safe_parse(self, data_source: str) -> ParseResult | None:
        """Parse a connection URL, normalizing the scheme first.

        urllib's scheme grammar forbids '_' and '+', so a canonical SQLAlchemy
        URL like ``oracle+cx_oracle://user:pass@host`` would otherwise fail to
        parse and its netloc (with credentials) would be silently missed.
        Replacing the scheme with a placeholder ``x`` lets urlparse extract the
        netloc correctly. Returns None if the URL is too malformed to parse.
        """
        target = (
            f"x://{rest}"
            if "://" in data_source and (rest := data_source.split("://", 1)[1])
            else data_source
        )
        try:
            return urlparse(target)
        except ValueError:
            return None

    def _has_embedded_credentials(self, data_source: str) -> bool:
        """True if the connection string embeds userinfo.

        Detects on the RAW string (an '@' anywhere) rather than urlparse's
        userinfo extraction, because urlparse truncates the netloc at '#', '?',
        or '/' — so a password containing those chars (e.g. ``p#x@host``) puts
        the '@' in the fragment/query/path and urlparse reports no userinfo,
        silently leaking. For SQL connection strings an '@' is the reliable
        userinfo signal; non-credential URLs (sqlite path, ``host/db``) have none.
        """
        return "@" in data_source

    def _redact_connection_string(self, data_source: str) -> str:
        """Drop userinfo for display, fail-CLOSED.

        When urlparse cleanly extracted userinfo we rebuild scheme+host:port (no
        creds) so the host stays visible. When it could NOT cleanly extract
        (password held '#', '?', '/', or the URL is malformed — urlparse reports
        no userinfo even though '@' is present) we return a constant placeholder
        rather than echo any part of the raw string.
        """
        parsed = self._safe_parse(data_source)
        if parsed is not None and (parsed.username or parsed.password):
            scheme = data_source.split("://", 1)[0] if "://" in data_source else parsed.scheme
            host = parsed.hostname or ""
            try:
                port = parsed.port
            except ValueError:
                port = None
            netloc = f"{host}:{port}" if port else host
            # also drop query/fragment — a secret placed there (non-standard, but
            # possible) would otherwise survive into the advisory display.
            return urlunparse(parsed._replace(scheme=scheme, netloc=netloc, query="", fragment=""))
        return "<redacted-connection>"

    # --- code generators -------------------------------------------------------

    def _generate_pandas(
        self,
        query: str,
        data_source: str,
        source_type: str,
        numeric_cols: list[str] | None = None,
        cat_cols: list[str] | None = None,
    ) -> str:
        """Generate pandas code for CSV/Parquet."""
        read_func = "pd.read_csv" if source_type == "csv" else "pd.read_parquet"
        intents = self._detect_intents(query)
        num = self._extract_number(query) or 10

        header = [
            "import pandas as pd",
            f"df = {read_func}(r'{data_source}')",
            f"# User query: {query}",
            f"# Detected intents: {', '.join(intents) if intents else 'general exploration'}",
            "",
        ]

        # Schema-aware path: reference REAL column names from data_profile.
        if numeric_cols is not None or cat_cols is not None:
            return self._schema_aware_pandas(
                query, header, intents, num, numeric_cols or [], cat_cols or []
            )

        if not intents:
            return "\n".join(header + ["result = df.head()", "print(result)"])

        if "isnull" in intents:
            return "\n".join(
                header
                + [
                    "missing = df.isnull().sum().sort_values(ascending=False)",
                    "print('Missing values per column:')",
                    "print(missing[missing > 0])",
                ]
            )
        if "describe" in intents:
            return "\n".join(header + ["result = df.describe(include='all')", "print(result)"])
        if "corr" in intents:
            return "\n".join(
                header
                + [
                    "numeric_df = df.select_dtypes(include='number')",
                    "result = numeric_df.corr()",
                    "print(result)",
                ]
            )
        if "unique" in intents:
            return "\n".join(
                header
                + [
                    "for col in df.select_dtypes(include='object').columns:",
                    "    print(f'{col}: {df[col].nunique()} unique values')",
                ]
            )

        lines = header + [
            "# Infer numeric columns for aggregation",
            "numeric_cols = df.select_dtypes(include='number').columns.tolist()",
            "cat_cols = df.select_dtypes(include='object').columns.tolist()",
            "",
        ]

        if "groupby" in intents and "mean" in intents:
            lines += [
                "if cat_cols and numeric_cols:",
                "    result = df.groupby(cat_cols[0])[numeric_cols].mean().reset_index()",
                "else:",
                "    result = df.mean(numeric_only=True)",
            ]
        elif "groupby" in intents and "sum" in intents:
            lines += [
                "if cat_cols and numeric_cols:",
                "    result = df.groupby(cat_cols[0])[numeric_cols].sum().reset_index()",
                "else:",
                "    result = df.sum(numeric_only=True)",
            ]
        elif "groupby" in intents and "count" in intents:
            lines += [
                "if cat_cols:",
                "    result = df.groupby(cat_cols[0]).size().reset_index(name='count')",
                "else:",
                "    result = df.count()",
            ]
        elif "mean" in intents:
            lines.append("result = df[numeric_cols].mean()")
        elif "sum" in intents:
            lines.append("result = df[numeric_cols].sum()")
        elif "count" in intents:
            lines.append("result = df.count()")
        elif "nlargest" in intents:
            lines += [
                "if numeric_cols:",
                f"    result = df.nlargest({num}, numeric_cols[0])",
                "else:",
                "    result = df.head()",
            ]
        elif "nsmallest" in intents:
            lines += [
                "if numeric_cols:",
                f"    result = df.nsmallest({num}, numeric_cols[0])",
                "else:",
                "    result = df.head()",
            ]
        elif "sort_values" in intents:
            lines += [
                "if numeric_cols:",
                f"    result = df.sort_values(by=numeric_cols[0], ascending=False).head({num})",
                "else:",
                "    result = df.head()",
            ]
        elif "filter" in intents:
            lines += [
                "# Attempt a generic filter on the first numeric column > median",
                "if numeric_cols:",
                "    median_val = df[numeric_cols[0]].median()",
                "    result = df[df[numeric_cols[0]] > median_val]",
                "else:",
                "    result = df.head()",
            ]
        else:
            lines.append("result = df.head()")

        lines.append("print(result)")
        return "\n".join(lines)

    def _schema_aware_pandas(
        self,
        query: str,
        header: list[str],
        intents: list[str],
        num: int,
        numeric_cols: list[str],
        cat_cols: list[str],
    ) -> str:
        """Schema-aware pandas code: reference REAL column names from the schema."""
        num_col = self._pick_numeric_column(query, numeric_cols) if numeric_cols else None
        cat_col = cat_cols[0] if cat_cols else None
        lines = list(header)
        if not intents:
            lines += ["result = df.head()", "print(result)"]
            return "\n".join(lines)
        if "isnull" in intents:
            lines += [
                "missing = df.isnull().sum().sort_values(ascending=False)",
                "print('Missing values per column:')",
                "print(missing[missing > 0])",
            ]
            return "\n".join(lines)
        if "describe" in intents:
            lines += ["result = df.describe(include='all')", "print(result)"]
            return "\n".join(lines)
        if "corr" in intents:
            cols = "[" + ", ".join(repr(c) for c in numeric_cols) + "]" if numeric_cols else ""
            lines += [
                f"result = df[{cols}].corr()"
                if numeric_cols
                else "result = df.select_dtypes(include='number').corr()",
                "print(result)",
            ]
            return "\n".join(lines)

        # groupby / aggregate / sort / filter with real column names
        if "groupby" in intents and cat_col and num_col:
            if "size" in intents or "count" in intents:
                lines += [
                    f"result = df.groupby({cat_col!r}).size().reset_index(name='count')",
                    "print(result)",
                ]
            else:
                agg = ".sum()" if "sum" in intents else ".mean()"
                lines += [
                    f"result = df.groupby({cat_col!r})[{num_col!r}]{agg}.reset_index()",
                    "print(result)",
                ]
            return "\n".join(lines)
        if "nlargest" in intents and num_col:
            lines += [f"result = df.nlargest({num}, {num_col!r})", "print(result)"]
            return "\n".join(lines)
        if "nsmallest" in intents and num_col:
            lines += [f"result = df.nsmallest({num}, {num_col!r})", "print(result)"]
            return "\n".join(lines)
        if "sort_values" in intents and num_col:
            lines += [
                f"result = df.sort_values(by={num_col!r}, ascending=False).head({num})",
                "print(result)",
            ]
            return "\n".join(lines)
        if "mean" in intents and num_col:
            lines += [f"result = df[{num_col!r}].mean()", "print(result)"]
            return "\n".join(lines)
        if "sum" in intents and num_col:
            lines += [f"result = df[{num_col!r}].sum()", "print(result)"]
            return "\n".join(lines)
        if "count" in intents:
            lines += ["result = df.count()", "print(result)"]
            return "\n".join(lines)
        if "filter" in intents and num_col:
            lines += [
                f"median_val = df[{num_col!r}].median()",
                f"result = df[df[{num_col!r}] > median_val]",
                "print(result)",
            ]
            return "\n".join(lines)
        lines += ["result = df.head()", "print(result)"]
        return "\n".join(lines)

    def _generate_sql(self, query: str, data_source: str) -> str:
        """Generate SQL code based on query intent.

        If the connection string carries embedded credentials, the generated
        code reads it from ``$DB_URL`` (not inlined) so credentials never appear
        in the code or trajectory.
        """
        intents = self._detect_intents(query)
        num = self._extract_number(query) or 10

        if self._has_embedded_credentials(data_source):
            engine_lines = [
                "import os",
                "from sqlalchemy import create_engine",
                "# credentials via environment — never hardcode the connection string",
                "engine = create_engine(os.environ['DB_URL'])",
            ]
        else:
            engine_lines = [
                "from sqlalchemy import create_engine",
                f"engine = create_engine(r'{data_source}')",
            ]

        lines = [
            "import pandas as pd",
            *engine_lines,
            f"# User query: {query}",
            f"# Detected intents: {', '.join(intents) if intents else 'general exploration'}",
            "",
            "# NOTE: Replace 'table_name' with the actual table name",
            "table_name = 'table_name'",
            "",
        ]

        select_parts = ["SELECT *"]
        from_clause = "FROM {table_name}"
        where_clause = ""
        order_clause = ""
        limit_clause = f"LIMIT {num}"

        if "count" in intents:
            select_parts = ["SELECT COUNT(*)"]
            limit_clause = ""

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

        lines += [
            'query_sql = """',
            "\n".join(sql_lines),
            '"""',
            "",
            "result = pd.read_sql(query_sql, engine)",
            "print(result)",
        ]

        return "\n".join(lines)

    def _generate_dataframe(
        self,
        query: str,
        data_source: str,
        numeric_cols: list[str] | None = None,
        cat_cols: list[str] | None = None,
    ) -> str:
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

        if numeric_cols is not None or cat_cols is not None:
            num_col = self._pick_numeric_column(query, numeric_cols or [])
            if not intents:
                lines.append("result = df.head()")
            elif "describe" in intents:
                lines.append("result = df.describe(include='all')")
            elif "isnull" in intents:
                lines.append("result = df.isnull().sum().sort_values(ascending=False)")
            elif "corr" in intents:
                cols = (
                    "[" + ", ".join(repr(c) for c in (numeric_cols or [])) + "]"
                    if numeric_cols
                    else ""
                )
                lines.append(
                    f"result = df[{cols}].corr()"
                    if numeric_cols
                    else "result = df.select_dtypes(include='number').corr()"
                )
            elif "nlargest" in intents and num_col:
                lines.append(f"result = df.nlargest({num}, {num_col!r})")
            elif "nsmallest" in intents and num_col:
                lines.append(f"result = df.nsmallest({num}, {num_col!r})")
            elif "mean" in intents and num_col:
                lines.append(f"result = df[{num_col!r}].mean()")
            elif "sum" in intents and num_col:
                lines.append(f"result = df[{num_col!r}].sum()")
            else:
                lines.append("result = df.head()")
            lines += ["if result is not None:", "    print(result)"]
            return "\n".join(lines)

        if not intents:
            lines.append("result = df.head()")
        elif "describe" in intents:
            lines.append("result = df.describe(include='all')")
        elif "isnull" in intents:
            lines.append("result = df.isnull().sum().sort_values(ascending=False)")
        elif "corr" in intents:
            lines.append("result = df.select_dtypes(include='number').corr()")
        elif "nlargest" in intents:
            lines += [
                "numeric_cols = df.select_dtypes(include='number').columns.tolist()",
                "if numeric_cols:",
                f"    result = df.nlargest({num}, numeric_cols[0])",
                "else:",
                "    result = df.head()",
            ]
        elif "nsmallest" in intents:
            lines += [
                "numeric_cols = df.select_dtypes(include='number').columns.tolist()",
                "if numeric_cols:",
                f"    result = df.nsmallest({num}, numeric_cols[0])",
                "else:",
                "    result = df.head()",
            ]
        elif "mean" in intents:
            lines.append("result = df.select_dtypes(include='number').mean()")
        elif "sum" in intents:
            lines.append("result = df.select_dtypes(include='number').sum()")
        elif "count" in intents:
            lines.append("result = df.count()")
        elif "unique" in intents:
            lines += [
                "for col in df.select_dtypes(include='object').columns:",
                "    print(f'{col}: {df[col].nunique()} unique values')",
            ]
            lines.append("result = None")
        else:
            lines.append("result = df.head()")

        lines += ["if result is not None:", "    print(result)"]
        return "\n".join(lines)
