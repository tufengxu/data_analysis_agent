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
