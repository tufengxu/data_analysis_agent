"""Tests for the DataQualityTool: read-only quality checks on a single table.

Covers the eight advisory flags + table-level metrics + Excel sheet handling +
the fail-closed path policy, mirroring ``test_data_profile.py``. Type-anomaly
flags (numeric/date stored as text) are asserted through the real
``_column_quality`` against in-memory frames, because pandas' CSV type
inference makes a high-numeric-ratio *object* column brittle to construct from
a plain file; the core flags are exercised end-to-end through real files.
"""

from __future__ import annotations

import pytest

from data_analysis_agent.tools import data_quality as dq
from data_analysis_agent.tools.data_quality import DataQualityTool

# --- schema / security flags -------------------------------------------------


def test_schema_and_security_flags():
    tool = DataQualityTool()
    assert tool.name == "data_quality"
    # read-only quality check: safe, non-destructive, parallelizable
    assert tool.is_read_only({}) is True
    assert tool.is_destructive({}) is False
    assert tool.is_concurrency_safe({}) is True


def test_validate_requires_path():
    tool = DataQualityTool()
    assert tool.validate_input({}).valid is False
    assert tool.validate_input({"path": ""}).valid is False
    assert tool.validate_input({"path": "data.csv"}).valid is True


def test_id_name_heuristic_is_token_based():
    # token match (so "no" does NOT fire on "note"); camelCase/PascalCase split
    assert dq._is_id_like_name("order_id") is True
    assert dq._is_id_like_name("CustomerNum") is True
    assert dq._is_id_like_name("OrderId") is True
    assert dq._is_id_like_name("APIKey") is True
    assert dq._is_id_like_name("uid") is True
    assert dq._is_id_like_name("note") is False
    assert dq._is_id_like_name("amount") is False


# --- end-to-end through real files ------------------------------------------


async def test_csv_detects_missingness_and_duplicate_rows(tmp_path):
    csv = tmp_path / "sales.csv"
    # amount has one missing + one row is a full duplicate of another
    csv.write_text(
        "order_id,region,amount\n1,East,10\n2,West,20\n1,East,10\n3,East,\n",
        encoding="utf-8",
    )
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    assert not result.is_error
    table = result.metadata["quality"]["tables"][0]
    assert table["n_rows"] == 4
    assert table["n_duplicate_rows"] == 1
    cols = {c["name"]: c for c in table["columns"]}
    assert cols["amount"]["n_missing"] == 1
    assert cols["amount"]["missing_pct"] == 25.0
    assert str(csv.resolve()) in result.content


async def test_constant_column_flag(tmp_path):
    csv = tmp_path / "c.csv"
    csv.write_text("k,v\n1,5\n2,5\n3,5\n", encoding="utf-8")
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    col = {c["name"]: c for c in result.metadata["quality"]["tables"][0]["columns"]}["v"]
    assert col["is_constant"] is True
    assert "constant" in col["flags"]


async def test_all_unique_and_identifier_flags(tmp_path):
    csv = tmp_path / "ids.csv"
    csv.write_text("order_id,amt\n1,10\n2,20\n3,30\n", encoding="utf-8")
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    col = {c["name"]: c for c in result.metadata["quality"]["tables"][0]["columns"]}["order_id"]
    assert "all_unique" in col["flags"]
    assert "identifier_like" in col["flags"]


async def test_duplicate_key_risk_flag(tmp_path):
    csv = tmp_path / "dups.csv"
    # order_id looks like a key but repeats -> duplicate_key_risk, NOT all_unique
    csv.write_text("order_id,v\n1,10\n1,20\n2,30\n", encoding="utf-8")
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    col = {c["name"]: c for c in result.metadata["quality"]["tables"][0]["columns"]}["order_id"]
    assert "duplicate_key_risk" in col["flags"]
    assert "all_unique" not in col["flags"]
    assert "identifier_like" not in col["flags"]


async def test_numeric_outliers_iqr(tmp_path):
    csv = tmp_path / "out.csv"
    # mostly small values + two extreme points -> IQR outliers > 5%
    rows = ["amt"] + [str(i) for i in range(1, 9)] + ["1000", "2000"]
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    col = {c["name"]: c for c in result.metadata["quality"]["tables"][0]["columns"]}["amt"]
    assert col["numeric"]["n_outliers"] == 2
    assert "high_outliers" in col["flags"]


async def test_high_missing_flag(tmp_path):
    csv = tmp_path / "sparse.csv"
    # Two columns so empty cells are NOT skipped as blank lines (single-column
    # blank rows are dropped by pandas). Column a is 75% missing (3 of 4).
    csv.write_text("a,b\n,1\n,2\n,3\n4,5\n", encoding="utf-8")
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    col = {c["name"]: c for c in result.metadata["quality"]["tables"][0]["columns"]}["a"]
    assert col["n_missing"] == 3
    assert col["missing_pct"] == 75.0
    assert "high_missing" in col["flags"]


async def test_numeric_block_present_for_numeric_column(tmp_path):
    csv = tmp_path / "n.csv"
    csv.write_text("a\n1\n2\n3\n", encoding="utf-8")
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    col = result.metadata["quality"]["tables"][0]["columns"][0]
    assert "numeric" in col
    assert col["numeric"]["min"] == 1.0
    assert col["numeric"]["max"] == 3.0
    assert col["numeric"]["mean"] == 2.0


async def test_excel_checks_all_sheets_by_default(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    xlsx = tmp_path / "book.xlsx"
    with pd.ExcelWriter(xlsx) as writer:
        pd.DataFrame({"order_id": [1, 1, 2], "v": [10, 10, 30]}).to_excel(
            writer, sheet_name="orders", index=False
        )
        pd.DataFrame({"code": ["a", "b"]}).to_excel(writer, sheet_name="ref", index=False)
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(xlsx)})

    tables = result.metadata["quality"]["tables"]
    assert {t["sheet"] for t in tables} == {"orders", "ref"}
    orders = next(t for t in tables if t["sheet"] == "orders")
    assert orders["n_duplicate_rows"] == 1


async def test_excel_specific_sheet_only(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    xlsx = tmp_path / "book.xlsx"
    with pd.ExcelWriter(xlsx) as writer:
        pd.DataFrame({"a": [1, 2]}).to_excel(writer, sheet_name="s1", index=False)
        pd.DataFrame({"b": [3, 4]}).to_excel(writer, sheet_name="s2", index=False)
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(xlsx), "sheet": "s2"})

    tables = result.metadata["quality"]["tables"]
    assert [t["sheet"] for t in tables] == ["s2"]
    assert tables[0]["columns"][0]["name"] == "b"


async def test_excel_missing_sheet_is_error(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    xlsx = tmp_path / "book.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(xlsx, sheet_name="s1", index=False)
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(xlsx), "sheet": "nope"})

    assert result.is_error
    assert "sheet" in result.content.lower()


async def test_truncated_large_csv_sets_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(dq, "_MAX_ROWS", 3)
    csv = tmp_path / "big.csv"
    csv.write_text("a\n1\n2\n3\n4\n5\n", encoding="utf-8")  # 5 data rows, cap 3
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    report = result.metadata["quality"]
    assert report["truncated"] is True
    assert report["tables"][0]["n_rows"] == 3  # capped, not 5
    assert "WARNING" in result.content
    assert "3" in result.content  # the cap value surfaces in the warning


async def test_empty_csv_returns_empty_profile_not_error(tmp_path):
    csv = tmp_path / "empty.csv"
    csv.write_text("", encoding="utf-8")
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(csv)})

    assert not result.is_error
    table = result.metadata["quality"]["tables"][0]
    assert table["n_rows"] == 0


async def test_path_outside_allowed_is_error(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "secret.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    tool = DataQualityTool(allowed_paths=[allowed])

    result = await tool.call({"path": str(outside)})

    assert result.is_error
    assert "outside" in result.content.lower()


async def test_missing_path_is_error(tmp_path):
    tool = DataQualityTool(allowed_paths=[tmp_path])
    result = await tool.call({"path": str(tmp_path / "nope.csv")})
    assert result.is_error


async def test_unsupported_file_type_is_error(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n")
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(img)})

    assert result.is_error


async def test_directory_is_unsupported(tmp_path):
    """data_quality is file-only; directory listing is data_profile's job."""
    tool = DataQualityTool(allowed_paths=[tmp_path])
    result = await tool.call({"path": str(tmp_path)})
    assert result.is_error
    assert "unsupported" in result.content.lower()


# --- type-anomaly flags via the real _column_quality (object frames) ---------


def test_numeric_stored_as_text_flag():
    pd = pytest.importorskip("pandas")
    # string-typed column, ~90% numeric-parseable -> numeric_stored_as_text.
    # (pandas 3.x gives a `str` dtype here; older versions give `object` — the
    # flag logic treats both the same, so assert behaviour, not the dtype name.)
    col = dq._column_quality(
        pd.DataFrame({"val": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "abc"]}),
        "val",
        10,
    )
    assert "numeric" not in col  # routed to the text path, not numeric
    assert col["text"]["numeric_ratio"] >= 0.9
    assert "numeric_stored_as_text" in col["flags"]


def test_date_stored_as_text_flag():
    pd = pytest.importorskip("pandas")
    col = dq._column_quality(
        pd.DataFrame({"d": ["2024-01-01", "2024-01-02", "2024-01-03"]}),
        "d",
        3,
    )
    assert col["text"]["date_ratio"] == 1.0
    assert "date_stored_as_text" in col["flags"]


def test_clean_text_column_has_no_type_anomaly_flag():
    pd = pytest.importorskip("pandas")
    col = dq._column_quality(
        pd.DataFrame({"region": ["East", "West", "East", "North"]}),
        "region",
        4,
    )
    assert "numeric_stored_as_text" not in col["flags"]
    assert "date_stored_as_text" not in col["flags"]


def test_real_datetime_column_not_flagged_as_text():
    """A genuine datetime64 column must NOT get date_stored_as_text (MAJOR-1)."""
    pd = pytest.importorskip("pandas")
    col = dq._column_quality(
        pd.DataFrame({"ts": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])}),
        "ts",
        3,
    )
    assert "text" not in col  # routed to the datetime block, not text
    assert "datetime" in col
    assert "date_stored_as_text" not in col["flags"]
    assert "numeric_stored_as_text" not in col["flags"]


def test_category_column_is_base_only_without_type_anomaly_flag():
    """A categorical-dtype column is already clean: base stats only, no anomaly flag."""
    pd = pytest.importorskip("pandas")
    col = dq._column_quality(
        pd.DataFrame({"cat": pd.Categorical(["a", "b", "a", "b"])}),
        "cat",
        4,
    )
    assert "numeric_stored_as_text" not in col["flags"]
    assert "date_stored_as_text" not in col["flags"]


def test_complex_column_not_silently_coerced_to_numeric():
    """Complex dtype must not go through the numeric path (would drop imaginary)."""
    pd = pytest.importorskip("pandas")
    col = dq._column_quality(
        pd.DataFrame({"z": [complex(1, 2), complex(3, 4)]}),
        "z",
        2,
    )
    assert "numeric" not in col


def test_bool_column_is_base_only_no_false_outliers():
    """pandas treats bool as numeric; an imbalanced bool column must NOT get
    high_outliers (is_refunded/is_fraud-style columns)."""
    pd = pytest.importorskip("pandas")
    col = dq._column_quality(
        pd.DataFrame({"is_vip": [False] * 9 + [True]}),
        "is_vip",
        10,
    )
    assert "numeric" not in col  # routed to base-only, not numeric
    assert "high_outliers" not in col["flags"]


def test_all_missing_column_not_flagged_constant():
    """All-NaN column is empty, not constant (MINOR-5): n_unique==0."""
    pd = pytest.importorskip("pandas")
    col = dq._column_quality(
        pd.DataFrame({"a": [float("nan"), float("nan"), float("nan")]}),
        "a",
        3,
    )
    assert col["n_unique"] == 0
    assert col["is_constant"] is False
    assert "constant" not in col["flags"]


# --- parquet (only runs where a parquet engine is installed) ----------------


async def test_parquet_end_to_end(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")  # default install has no parquet engine
    df = pd.DataFrame({"order_id": [1, 1, 2], "amount": [10, 10, None]})
    pq_path = tmp_path / "data.parquet"
    df.to_parquet(pq_path)
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(pq_path)})

    assert not result.is_error
    report = result.metadata["quality"]
    assert report["format"] == "parquet"
    assert report["truncated"] is False  # small file is under the row cap
    table = report["tables"][0]
    assert table["n_duplicate_rows"] == 1
    cols = {c["name"]: c for c in table["columns"]}
    assert cols["amount"]["n_missing"] == 1


def test_read_parquet_uses_capped_reader_and_falls_back(monkeypatch):
    """Parquet reads go through the _MAX_ROWS-capped row-group reader (review MAJOR),
    and fall back to a plain read only when pyarrow is genuinely absent.

    A sys.modules pyarrow shim is unsafe (importing pyarrow.parquet makes pandas
    eagerly init its Arrow backend and demand dozens of real attrs), so this
    patches the isolated ``_capped_parquet_df`` seam instead of faking the engine.
    The cap loop itself is covered by test_capped_parquet_df_* below.
    """
    pd = pytest.importorskip("pandas")

    # Path 1: capped reader is used; its (df, truncated) is returned verbatim.
    seen = {}

    def _fake_capped(path, max_rows):
        seen["max_rows"] = max_rows
        return pd.DataFrame({"a": [1, 2]}), False

    monkeypatch.setattr(dq, "_capped_parquet_df", _fake_capped)
    df, truncated = dq._read_parquet("fake.parquet")
    assert truncated is False
    assert list(df["a"]) == [1, 2]
    assert seen["max_rows"] == dq._MAX_ROWS  # cap is threaded through

    # Path 2: pyarrow absent -> ImportError from the capped reader triggers the
    # plain-read fallback (which itself errors here since no engine/file, but the
    # fallback path is what we assert via the raised error type changing).
    def _no_pyarrow(path, max_rows):
        raise ImportError("No module named 'pyarrow'")

    monkeypatch.setattr(dq, "_capped_parquet_df", _no_pyarrow)
    monkeypatch.setattr(pd, "read_parquet", lambda p: pd.DataFrame({"b": [9]}))
    df, truncated = dq._read_parquet("fake.parquet")
    assert truncated is False
    assert list(df["b"]) == [9]  # fell back to plain pd.read_parquet


def _rg(df):
    """A minimal RecordBatch/Table stand-in (slice/to_pandas/num_rows)."""

    class _RG:
        def __init__(self, frame):
            self._df = frame
            self.num_rows = len(frame)

        def slice(self, off, n):
            return _RG(self._df.iloc[off : off + n])

        def to_pandas(self):
            return self._df

    return _RG(df)


def _pa_pq_shims(batches, columns=None):
    """Build (pyarrow, pyarrow.parquet) module shims that stream ``batches``.

    ``columns`` is the schema the empty-file path should preserve (defaults to
    the union of the batches' columns).
    """
    import types

    import pandas as pd

    if columns is None:
        columns = list(batches[0]._df.columns) if batches else []

    pa = types.ModuleType("pyarrow")
    # from_batches must accept (batches, schema=None); concat_tables is
    # intentionally ABSENT so a regression that calls it fails loudly.
    pa.Table = types.SimpleNamespace(
        from_batches=lambda bs, schema=None: _rg(
            pd.concat([b._df for b in bs], ignore_index=True)
            if bs
            else pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
        )
    )

    class _PF:
        metadata = types.SimpleNamespace(num_rows=sum(b.num_rows for b in batches))
        schema_arrow = types.SimpleNamespace(names=columns)

        def iter_batches(self, batch_size):
            return iter(batches)

    pq = types.ModuleType("pyarrow.parquet")
    pq.ParquetFile = lambda path: _PF()
    pa.parquet = pq
    return pa, pq


def test_capped_parquet_df_caps_across_row_groups(monkeypatch):
    """The cap loop stops at max_rows across streamed batch boundaries."""
    pd = pytest.importorskip("pandas")
    import sys

    batches = [
        _rg(pd.DataFrame({"a": range(8)})),
        _rg(pd.DataFrame({"a": range(9)})),
        _rg(pd.DataFrame({"a": range(8)})),
    ]
    pa, pq = _pa_pq_shims(batches)
    monkeypatch.setitem(sys.modules, "pyarrow", pa)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", pq)

    df, truncated = dq._capped_parquet_df("fake.parquet", 10)
    assert truncated is True
    assert len(df) == 10  # 8 + 2 (second batch sliced)


def test_capped_parquet_df_under_cap_reads_all(monkeypatch):
    """Under the cap the full table is read and not flagged."""
    pd = pytest.importorskip("pandas")
    import sys

    batches = [_rg(pd.DataFrame({"a": range(4)}))]
    pa, pq = _pa_pq_shims(batches)
    monkeypatch.setitem(sys.modules, "pyarrow", pa)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", pq)

    df, truncated = dq._capped_parquet_df("fake.parquet", 10)
    assert truncated is False
    assert len(df) == 4


def test_capped_parquet_df_single_oversized_batch_stays_capped(monkeypatch):
    """A single huge 'row group' (the pandas to_parquet default) must NOT be
    fully materialized: the streaming loop caps it at max_rows (review MAJOR)."""
    pd = pytest.importorskip("pandas")
    import sys

    # one batch far over the cap — iter_batches semantics mean the engine yields
    # bounded chunks, but even a single oversized yielded batch is sliced.
    batches = [_rg(pd.DataFrame({"a": range(1000)}))]
    pa, pq = _pa_pq_shims(batches)
    monkeypatch.setitem(sys.modules, "pyarrow", pa)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", pq)

    df, truncated = dq._capped_parquet_df("fake.parquet", 10)
    assert truncated is True
    assert len(df) == 10


def test_capped_parquet_df_multi_batch_uses_from_batches_not_concat(monkeypatch):
    """Multi-batch combine must go through Table.from_batches (RecordBatch-safe),
    NOT pa.concat_tables (which segfaults on RecordBatch — apache/arrow#47000).
    The shim omits concat_tables entirely, so a regression calling it errors."""
    pd = pytest.importorskip("pandas")
    import sys

    # >batch_size rows forces the multi-batch combine branch.
    batches = [
        _rg(pd.DataFrame({"a": range(8)})),
        _rg(pd.DataFrame({"a": range(9)})),
        _rg(pd.DataFrame({"a": range(8)})),
    ]
    pa, pq = _pa_pq_shims(batches)
    assert not hasattr(pa, "concat_tables")  # regression would AttributeError, loudly
    monkeypatch.setitem(sys.modules, "pyarrow", pa)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", pq)

    df, truncated = dq._capped_parquet_df("fake.parquet", 100)
    assert truncated is False
    assert len(df) == 25  # all three batches combined via from_batches


def test_capped_parquet_df_empty_preserves_schema(monkeypatch):
    """A 0-row parquet keeps its columns (like pd.read_parquet), not a shapeless frame."""
    pytest.importorskip("pandas")
    import sys

    pa, pq = _pa_pq_shims([], columns=["order_id", "amount"])
    monkeypatch.setitem(sys.modules, "pyarrow", pa)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", pq)

    df, truncated = dq._capped_parquet_df("fake.parquet", 10)
    assert truncated is False
    assert len(df) == 0
    assert list(df.columns) == ["order_id", "amount"]


async def test_parquet_over_cap_is_truncated_end_to_end(tmp_path, monkeypatch):
    """Real-engine check: a multi-row-group parquet over _MAX_ROWS is truncated."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")  # needs a real engine to WRITE the fixture
    import pyarrow  # noqa: F401 — confirm a usable engine, not a leftover shim
    monkeypatch.setattr(dq, "_MAX_ROWS", 3)
    df = pd.DataFrame({"a": range(9)})
    pq_path = tmp_path / "big.parquet"
    df.to_parquet(pq_path, row_group_size=3)  # 3 row groups of 3 rows
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(pq_path)})

    assert not result.is_error
    report = result.metadata["quality"]
    assert report["truncated"] is True
    assert report["tables"][0]["n_rows"] == 3


async def test_parquet_missing_engine_error_message(tmp_path, monkeypatch):
    """Without a parquet engine, the dedicated ImportError branch fires (MAJOR-2).

    Asserts the branch-unique install phrasing (not the words copied from the
    raised exception text), so the test fails if someone deletes the
    ``except ImportError`` branch and lets it fall through to the generic
    ``Error checking …`` handler.
    """
    pq_path = tmp_path / "data.parquet"
    pq_path.write_bytes(b"not really parquet")

    def _boom(_path):
        raise ImportError("no engine")

    monkeypatch.setattr(dq, "_read_parquet", _boom)
    tool = DataQualityTool(allowed_paths=[tmp_path])

    result = await tool.call({"path": str(pq_path)})

    assert result.is_error
    # Phrasing only the dedicated ImportError branch emits:
    assert "Install the 'data' extra" in result.content
    assert "pyarrow" in result.content


# --- registration guard ------------------------------------------------------


def test_data_quality_registered_and_read_only_classified():
    """Must be in build_registry AND READ_ONLY_TOOLS (else local_safe DENY's it)."""
    from data_analysis_agent.config import AgentConfig
    from data_analysis_agent.runtime import READ_ONLY_TOOLS, build_registry

    registry = build_registry(AgentConfig())
    names = {t.name for t in registry.get_all_base_tools()}
    assert "data_quality" in names
    assert "data_quality" in READ_ONLY_TOOLS
