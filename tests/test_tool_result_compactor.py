"""ToolResultCompactor 接缝契约测试(v2 采样压缩升级,spec 5.2/5.3)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_analysis_agent.capabilities.sampling import (
    CompactRequest,
    DefaultToolResultCompactor,
    SamplingConfig,
    collapse_digest,
    data_state_block,
    recall_hint,
)
from data_analysis_agent.capabilities.sampling.result_store import ResultStore
from data_analysis_agent.sampling import compact_result as v1_compact_result

SANDBOX_SUMMARY_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "data_analysis_agent"
    / "capabilities"
    / "sampling"
    / "sandbox_summary.py"
)


def _big_csv(rows: int = 500) -> str:
    lines = ["region,product,units,price"]
    for i in range(rows):
        lines.append(f"r{i % 7},p{i % 13},{i},{(i % 11) * 1.5:.2f}")
    return "\n".join(lines)


class TestContractShape:
    def test_passthrough_below_threshold(self) -> None:
        compactor = DefaultToolResultCompactor()
        result = compactor.compact(CompactRequest(content="small", max_chars=50_000))
        assert result.was_compacted is False
        assert result.content == "small"
        assert result.sampling_method == "passthrough"
        assert result.result_id is None

    def test_compact_large_table_annotates_method_and_fidelity(self) -> None:
        compactor = DefaultToolResultCompactor()
        content = _big_csv(1200)
        result = compactor.compact(
            CompactRequest(
                content=content,
                max_chars=50_000,
                context_pressure=0.9,  # 宽松接受率:确保增益门放行(v1 语义)
                config=SamplingConfig(fidelity_level="high"),
            )
        )
        assert result.was_compacted is True
        assert result.sampling_method == "table-summary"
        assert result.fidelity_level == "high"
        assert len(result.content) < len(content)

    def test_fidelity_and_thresholds_are_caller_configurable(self) -> None:
        compactor = DefaultToolResultCompactor()
        low = SamplingConfig(fidelity_level="low", max_sample_rows=4, top_k=2)
        result = compactor.compact(
            CompactRequest(
                content=_big_csv(1200), max_chars=50_000, context_pressure=0.9, config=low
            )
        )
        assert result.fidelity_level == "low"
        assert "fidelity=low" in result.content

    def test_context_pressure_is_a_contract_parameter(self) -> None:
        content = _big_csv(200)
        lenient = DefaultToolResultCompactor().compact(
            CompactRequest(content=content, max_chars=50_000, context_pressure=0.95)
        )
        strict = DefaultToolResultCompactor().compact(
            CompactRequest(content=content, max_chars=50_000, context_pressure=0.0)
        )
        # 高压更宽松(接受率更高):宽松档被压缩的概率 >= 严格档
        assert lenient.was_compacted >= strict.was_compacted


class TestRecallHandle:
    def test_compacted_original_is_retrievable_page_by_page(self, tmp_path: Path) -> None:
        store = ResultStore(tmp_path / "store")
        compactor = DefaultToolResultCompactor(store)
        original = _big_csv(600)
        result = compactor.compact(
            CompactRequest(
                content=original,
                max_chars=50_000,
                context_pressure=0.9,
                result_id="r-1",
                tool_name="t",
            )
        )
        assert result.result_id == "r-1"
        assert recall_hint("r-1") in result.content
        page = store.get("r-1", offset=0, limit=50)
        assert page is not None
        assert page.text.splitlines()[1] == original.splitlines()[0]
        assert page.tool == "t"

    def test_store_shared_across_instances(self, tmp_path: Path) -> None:
        """v2 多进程共享:两个 server 实例(如基座 mcp-client + 插件)共用同一目录互见。"""

        store_a = ResultStore(tmp_path / "shared")
        store_b = ResultStore(tmp_path / "shared")
        assert store_a.put("a-1", "hello\nworld", {"tool": "t"})
        assert store_b.put("b-1", "foo\nbar", {"tool": "t"})
        page_b = store_a.get("b-1")
        page_a = store_b.get("a-1")
        assert page_b is not None and "foo" in page_b.text
        assert page_a is not None and "hello" in page_a.text

    def test_no_store_means_no_handle(self) -> None:
        result = DefaultToolResultCompactor().compact(
            CompactRequest(
                content=_big_csv(1200), max_chars=50_000, context_pressure=0.9, result_id="r-2"
            )
        )
        assert result.result_id is None
        assert "retrieve_result" not in result.content


class TestV1Equivalence:
    def test_reference_impl_matches_v1_compact_result_output(self) -> None:
        content = _big_csv(400)
        v1_out, v1_was = v1_compact_result(content, 50_000, SamplingConfig(), 0.3)
        v2 = DefaultToolResultCompactor().compact(
            CompactRequest(content=content, max_chars=50_000, context_pressure=0.3)
        )
        assert v2.was_compacted == v1_was
        assert v2.content == v1_out

    def test_marker_is_byte_identical_to_v1_seam(self) -> None:
        assert (
            recall_hint("abc")
            == '[完整结果已缓存。回取: retrieve_result(result_id="abc", offset=0, limit=50)]'
        )

    def test_fail_closed_never_raises(self) -> None:
        compactor = DefaultToolResultCompactor()
        result = compactor.compact(CompactRequest(content="x" * 10, max_chars=1))
        assert isinstance(result.content, str)


class TestDataStateBlock:
    """D4:kernel 变量 + 存活回取 id → 紧凑数据态块(压缩后重注入用)。"""

    def test_frames_and_results(self) -> None:
        block = data_state_block(
            frames=[{"name": "orders", "rows": 12_000, "cols": 8}],
            results=[{"id": "toolu_1", "tool": "python_analysis", "bytes": 45_000}],
        )
        assert "orders: 12,000 行 × 8 列" in block
        assert "toolu_1" in block and "python_analysis" in block and "43KB" in block

    def test_empty_inputs_yield_empty_block(self) -> None:
        assert data_state_block() == ""
        assert data_state_block(frames=[], results=[]) == ""


class TestCollapseDigest:
    """D3:session 层 mask 旧 tool_result 时的一行 digest + 回取句柄助手。"""

    TABLE_SUMMARY = (
        "### 数据采样摘要 (sampled view)\n"
        "- rows=12,000 · cols=8 · method=stratified[category] · fidelity=mid\n"
        "…\n\n" + recall_hint("abc")
    )

    def test_table_summary_digest(self) -> None:
        assert collapse_digest(self.TABLE_SUMMARY) == (
            '[collapsed: table 12,000 rows × 8 cols · retrieve_result(result_id="abc")]'
        )

    def test_text_digest_without_shape(self) -> None:
        content = "### 文本结果摘要 (sampled view)\n…\n\n" + recall_hint("t9")
        assert collapse_digest(content) == (
            '[collapsed: summarized result · retrieve_result(result_id="t9")]'
        )

    def test_missing_markers_return_none(self) -> None:
        assert collapse_digest("plain text without markers") is None
        assert collapse_digest("") is None


class TestSandboxSelfContainmentGuard:
    """spec 5.2:sandbox_summary 自包含内联约束必须有测试守护(守护物理迁移后的真实文件)。"""

    def test_no_package_import_and_no_future(self) -> None:
        source = SANDBOX_SUMMARY_PATH.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import data_analysis_agent",))
            assert not stripped.startswith(("from data_analysis_agent",))
            assert not stripped.startswith("from __future__")

    def test_no_top_level_pandas_import(self) -> None:
        """pandas/numpy 只允许函数内惰性导入(顶层零依赖,保证可内联)。"""

        source = SANDBOX_SUMMARY_PATH.read_text(encoding="utf-8")
        for line in source.splitlines():
            if not line.startswith((" ", "\t")):  # 顶层(非缩进)语句
                stripped = line.strip()
                assert not stripped.startswith("import pandas")
                assert not stripped.startswith("import numpy")

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("n_rows", int),
            ("n_cols", int),
            ("columns", list),
            ("sample_rows", list),
            ("sampling_method", str),
            ("fidelity_level", str),
            ("notes", list),
        ],
    )
    def test_output_shape_matches_renderer_contract(self, header: str, expected: type) -> None:
        """输出 shape 与 TableSummary.to_dict 兼容(单一渲染器约束)。"""

        try:
            import pandas as pd
        except ImportError:
            pytest.skip("pandas not installed")

        from data_analysis_agent.capabilities.sampling.sandbox_summary import (
            summarize_dataframe,
        )

        rows = [{"a": i, "b": f"x{i % 3}"} for i in range(60)]
        summary = summarize_dataframe(pd.DataFrame(rows), max_sample_rows=5)
        assert header in summary
        assert isinstance(summary[header], expected)
