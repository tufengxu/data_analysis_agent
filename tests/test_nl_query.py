"""Tests for NlQueryTool: schema-aware codegen + SQL secret guard (P1-4.6)."""

from __future__ import annotations

import pytest

from data_analysis_agent.tools.nl_query import NlQueryTool

# --- back-compat: no schema, CSV --------------------------------------------


async def test_csv_without_schema_keeps_generic_behavior():
    tool = NlQueryTool()
    result = await tool.call(
        {"query": "Show top 10 products", "data_source": "/data/sales.csv", "source_type": "csv"}
    )
    assert not result.is_error
    assert "Generated query code" in result.content
    # generic path: references numeric_cols[0] (runtime detection), not a literal column
    code = result.metadata["generated_code"]
    assert "numeric_cols[0]" in code or "df.head()" in code


# --- schema-aware: real column names ----------------------------------------


async def test_schema_aware_uses_real_column_for_nlargest():
    tool = NlQueryTool()
    result = await tool.call(
        {
            "query": "top 10 products by revenue",
            "data_source": "/data/sales.csv",
            "source_type": "csv",
            "schema": [
                {"name": "product", "dtype": "object"},
                {"name": "revenue", "dtype": "int64"},
            ],
        }
    )
    code = result.metadata["generated_code"]
    # real column name "revenue" is selected (matched in the query), not numeric_cols[0]
    assert "'revenue'" in code
    assert "nlargest(10, 'revenue')" in code
    assert "numeric_cols[0]" not in code


async def test_schema_aware_picks_named_numeric_column_from_query():
    tool = NlQueryTool()
    result = await tool.call(
        {
            "query": "average price grouped by region",
            "data_source": "/data/x.csv",
            "source_type": "csv",
            "schema": [
                {"name": "region", "dtype": "object"},
                {"name": "price", "dtype": "float64"},
                {"name": "qty", "dtype": "int64"},
            ],
        }
    )
    code = result.metadata["generated_code"]
    # groupby on region (first categorical), agg on price (matched in query)
    assert "'price'" in code
    assert "'region'" in code
    assert "groupby" in code


async def test_schema_aware_dict_shape_accepted():
    # data_profile returns {columns: [...]} — accept that shape too
    tool = NlQueryTool()
    result = await tool.call(
        {
            "query": "sum of amount",
            "data_source": "/data/x.csv",
            "source_type": "csv",
            "schema": {"columns": [{"name": "amount", "dtype": "float64"}]},
        }
    )
    assert "'amount'" in result.metadata["generated_code"]


async def test_schema_aware_falls_back_when_no_numeric_match():
    tool = NlQueryTool()
    result = await tool.call(
        {
            "query": "top 5 rows",  # nlargest intent, no column name in query
            "data_source": "/data/x.csv",
            "source_type": "csv",
            "schema": [{"name": "amount", "dtype": "float64"}],
        }
    )
    # falls back to the first numeric column from the schema (still a real name)
    assert "'amount'" in result.metadata["generated_code"]


# --- SQL secret guard -------------------------------------------------------


async def test_sql_with_embedded_credentials_uses_env_not_inlined():
    tool = NlQueryTool()
    secret = "postgresql://alice:s3cr3t@db.host:5432/sales"
    result = await tool.call({"query": "count rows", "data_source": secret, "source_type": "sql"})
    code = result.metadata["generated_code"]
    # the credential MUST NOT appear in the generated code
    assert "s3cr3t" not in code
    assert "alice:s3cr3t" not in code
    # code reads from $DB_URL instead
    assert "os.environ['DB_URL']" in code
    # warning surfaced, display source redacted (userinfo dropped, host kept)
    assert any("credentials" in w for w in result.metadata["warnings"])
    assert "s3cr3t" not in result.content
    assert "alice" not in result.content
    assert "db.host" in result.content


async def test_sql_password_with_at_sign_fully_redacted():
    # a password containing '@' must not leak past the redaction (the naive regex
    # stopped at the first '@'; urlparse splits netloc at the LAST '@').
    tool = NlQueryTool()
    secret = "postgresql://bob:p@ss:w0rd!@db.host/sales"
    result = await tool.call({"query": "count", "data_source": secret, "source_type": "sql"})
    assert "p@ss" not in result.content
    assert "w0rd" not in result.content
    assert "ss:w0rd" not in result.content
    assert "p@ss:w0rd!" not in result.metadata["generated_code"]
    assert "db.host" in result.content  # host preserved (non-secret)


async def test_sql_driver_scheme_with_underscore_detected():
    # oracle+cx_oracle:// is SQLAlchemy's canonical Oracle URL; the driver name
    # contains '_' which urllib's scheme grammar rejects, so we normalize first.
    tool = NlQueryTool()
    secret = "oracle+cx_oracle://scott:t1ger@h.oracle.com:1521/?service_name=xe"
    result = await tool.call({"query": "count", "data_source": secret, "source_type": "sql"})
    assert "t1ger" not in result.content
    assert "scott:t1ger" not in result.metadata["generated_code"]
    assert "os.environ['DB_URL']" in result.metadata["generated_code"]


async def test_sql_malformed_url_fails_closed():
    # a malformed URL that urlparse cannot parse must NOT bypass the guard
    # (fail-closed: assume credentials present → env indirection, no inline).
    tool = NlQueryTool()
    secret = "postgresql://bob:p@ss@[::1:5432/db"  # unclosed IPv6 bracket
    result = await tool.call({"query": "count", "data_source": secret, "source_type": "sql"})
    assert "p@ss" not in result.content
    assert "p@ss" not in result.metadata["generated_code"]
    assert "os.environ['DB_URL']" in result.metadata["generated_code"]


async def test_sql_non_integer_port_does_not_crash():
    # parsed.port raises ValueError on a non-integer port; redaction must guard it
    # and call() must return a ToolResult (not raise).
    tool = NlQueryTool()
    secret = "postgresql://alice:pw@host:abc/db"
    result = await tool.call({"query": "count", "data_source": secret, "source_type": "sql"})
    # reaching here means no crash; credentials must still be guarded
    assert "alice:pw" not in result.metadata["generated_code"]
    assert "host:abc" not in result.content  # the bad port is redacted away
    assert "os.environ['DB_URL']" in result.metadata["generated_code"]


async def test_sql_query_fragment_dropped_from_display():
    # a secret placed in a query/fragment (non-standard) must not survive into
    # the advisory display; host+path are kept.
    tool = NlQueryTool()
    result = await tool.call(
        {
            "query": "count",
            "data_source": "postgresql://alice:s3cr3t@db.host:5432/sales?apikey=TOPSECRET#frag",
            "source_type": "sql",
        }
    )
    assert "TOPSECRET" not in result.content
    assert "s3cr3t" not in result.content
    assert "db.host" in result.content  # host kept
    assert "/sales" in result.content  # db path kept


@pytest.mark.parametrize(
    "secret",
    [
        "postgresql://alice:s3cr3t#x@db.host:5432/sales",  # '#' in password
        "postgresql://bob:p?secret@host/db",  # '?' in password
        "postgresql://u:p/secret@host/db",  # '/' in password
        "postgresql://bob:p#a#b@db.host/sales",  # multiple '#' in password
    ],
)
async def test_sql_password_with_netloc_terminator_detected(secret):
    # urlparse truncates netloc at '#', '?', '/', so its userinfo extraction
    # MISSES these — detection must use the raw '@' signal instead.
    tool = NlQueryTool()
    result = await tool.call({"query": "count", "data_source": secret, "source_type": "sql"})
    # the raw connection string must NOT appear in content or generated code
    assert secret not in result.content
    assert secret not in result.metadata["generated_code"]
    assert "os.environ['DB_URL']" in result.metadata["generated_code"]
    assert any("credentials" in w for w in result.metadata["warnings"])


async def test_parquet_double_dtype_treated_as_numeric():
    # pyarrow stringifies float64 as "double" — must still be numeric, not categorical
    tool = NlQueryTool()
    result = await tool.call(
        {
            "query": "average amount",
            "data_source": "/data/x.parquet",
            "source_type": "parquet",
            "schema": [{"name": "amount", "dtype": "double"}],
        }
    )
    code = result.metadata["generated_code"]
    assert "'amount'" in code
    assert ".mean()" in code  # aggregated, not degenerated to df.head()


async def test_schema_aware_handles_special_column_names():
    # column names with spaces / quotes must still generate valid python via repr()
    import ast

    tool = NlQueryTool()
    result = await tool.call(
        {
            "query": "top 5 by unit price",
            "data_source": "/data/x.csv",
            "source_type": "csv",
            "schema": [{"name": "unit price", "dtype": "float64"}],
        }
    )
    code = result.metadata["generated_code"]
    ast.parse(code)  # must be syntactically valid python
    assert "'unit price'" in code


async def test_sql_without_credentials_still_inlines():
    tool = NlQueryTool()
    result = await tool.call(
        {"query": "count rows", "data_source": "sqlite:///local.db", "source_type": "sql"}
    )
    code = result.metadata["generated_code"]
    # no credentials -> inline as before, no env indirection
    assert "create_engine(r'sqlite:///local.db')" in code
    assert "DB_URL" not in code
    assert result.metadata["warnings"] == []


def test_has_embedded_credentials_detection():
    tool = NlQueryTool()
    assert tool._has_embedded_credentials("postgresql://u:p@host/db") is True
    assert tool._has_embedded_credentials("mysql://user:pass@localhost/x") is True
    assert tool._has_embedded_credentials("sqlite:///local.db") is False
    assert tool._has_embedded_credentials("postgresql://host/db") is False  # no creds


def test_redact_connection_string_hides_credentials():
    tool = NlQueryTool()
    redacted = tool._redact_connection_string("postgresql://alice:s3cr3t@db.host/sales")
    assert "s3cr3t" not in redacted
    assert "alice" not in redacted
    assert "db.host" in redacted  # host preserved (non-secret)
