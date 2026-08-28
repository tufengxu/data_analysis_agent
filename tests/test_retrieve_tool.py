"""Tests for the retrieve_result tool."""

from __future__ import annotations

from data_analysis_agent.sampling.result_store import ResultStore
from data_analysis_agent.tools.retrieve_result import RetrieveResultTool


def _store(tmp_path):
    store = ResultStore(tmp_path / "r")
    store.put("t1", "\n".join(f"row{i}" for i in range(100)), {"tool": "big"})
    return store


def test_validate_requires_result_id(tmp_path):
    tool = RetrieveResultTool(_store(tmp_path))
    assert tool.validate_input({}).valid is False
    assert tool.validate_input({"result_id": "t1"}).valid is True


def test_validate_limit_bounds(tmp_path):
    tool = RetrieveResultTool(_store(tmp_path))
    assert tool.validate_input({"result_id": "t1", "limit": 0}).valid is False
    assert tool.validate_input({"result_id": "t1", "limit": 501}).valid is False
    assert tool.validate_input({"result_id": "t1", "limit": 500}).valid is True


def test_validate_offset_non_negative(tmp_path):
    tool = RetrieveResultTool(_store(tmp_path))
    assert tool.validate_input({"result_id": "t1", "offset": -1}).valid is False


async def test_call_returns_page(tmp_path):
    tool = RetrieveResultTool(_store(tmp_path))
    result = await tool.call({"result_id": "t1", "offset": 0, "limit": 3})
    assert result.is_error is False
    assert "row0" in result.content and "result_id=t1" in result.content


async def test_call_missing_id_is_error(tmp_path):
    tool = RetrieveResultTool(_store(tmp_path))
    result = await tool.call({"result_id": "nope"})
    assert result.is_error is True
    assert "not found or expired" in result.content


async def test_call_query_filter(tmp_path):
    store = ResultStore(tmp_path / "r")
    store.put("t1", "apple\nbanana\napricot", {})
    tool = RetrieveResultTool(store)
    result = await tool.call({"result_id": "t1", "query": "ap"})
    assert "apple" in result.content and "apricot" in result.content
    assert "banana" not in result.content.split("\n", 1)[1]


async def test_call_without_store_is_error(tmp_path):
    tool = RetrieveResultTool(None)
    result = await tool.call({"result_id": "t1"})
    assert result.is_error is True


def test_tool_is_read_only_and_safe(tmp_path):
    tool = RetrieveResultTool(_store(tmp_path))
    assert tool.is_read_only({}) is True
    assert tool.is_concurrency_safe({}) is True
    assert tool.is_destructive({}) is False


# --- D6: structured recall (mode/columns/filter) -----------------------------


async def _slicing_store(tmp_path):
    from data_analysis_agent.sampling.result_store import ResultStore

    store = ResultStore(tmp_path / "results")
    content = "\n".join(
        ["region,units,price"] + [f"{'abc'[i % 3]},{i},{i * 10}" for i in range(120)]
    )
    assert store.put("t2", content, {"tool": "big"}) is True
    return store


async def test_retrieve_slice_mode_and_filter(tmp_path):
    tool = RetrieveResultTool(result_store=await _slicing_store(tmp_path))
    result = await tool.call(
        {"result_id": "t2", "mode": "sample", "limit": 5, "filter": "units>=100"}
    )
    assert result.is_error is False
    assert result.content.startswith("[result_id=t2 | mode=sample")
    assert "of 20 matched / 120 rows" in result.content


async def test_retrieve_slice_projection(tmp_path):
    tool = RetrieveResultTool(result_store=await _slicing_store(tmp_path))
    result = await tool.call(
        {"result_id": "t2", "mode": "head", "limit": 3, "columns": ["region", "units"]}
    )
    assert result.is_error is False
    assert "| region | units |" in result.content


async def test_retrieve_slice_invalid_inputs(tmp_path):
    tool = RetrieveResultTool(result_store=await _slicing_store(tmp_path))
    bad_filter = tool.validate_input({"result_id": "t2", "filter": "units ~ 5"})
    assert bad_filter.valid is False
    bad_mode = tool.validate_input({"result_id": "t2", "mode": "sql"})
    assert bad_mode.valid is False
    unknown_col = await tool.call({"result_id": "t2", "mode": "head", "columns": ["missing"]})
    assert unknown_col.is_error is True
    assert "未知列" in unknown_col.content


async def test_retrieve_slice_non_table_degrades_to_error(tmp_path):
    from data_analysis_agent.sampling.result_store import ResultStore

    store = ResultStore(tmp_path / "results")
    store.put("t3", "loose prose\nwithout any table structure\n" * 20, {"tool": "x"})
    tool = RetrieveResultTool(result_store=store)
    result = await tool.call({"result_id": "t3", "mode": "head"})
    assert result.is_error is True
    assert "不是可解析的表格" in result.content
