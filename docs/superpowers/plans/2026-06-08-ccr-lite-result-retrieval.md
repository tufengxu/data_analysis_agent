# CCR-lite 结果可回取 + 收益门控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给被有损摘要的大工具结果加"原文可回取"(持久化 ResultStore + `retrieve_result` 工具)与"pressure-adaptive 收益门控",降低采样不可逆风险。

**Architecture:** 纯 stdlib 叶子 `sampling/result_store.py` 持久化原文(JSONL 索引 + 哈希文件名 + TTL/容量回收);`tools/retrieve_result.py` 按行分页回取;`compact_result` 增 `context_pressure` 做自适应门控;`agent_loop._execute_tools` 在有损压缩时存原文并在摘要尾部注入回取 marker。

**Tech Stack:** Python ≥3.10,stdlib(json/pathlib/hashlib/time/re/tempfile),pytest、ruff、mypy(strict)。分支 `feat/ccr-lite`。

**关联 spec:** `docs/superpowers/specs/2026-06-08-ccr-lite-result-retrieval-design.md`

---

## File Structure

| 文件                                               | 动作   | 职责                                          |
| -------------------------------------------------- | ------ | --------------------------------------------- |
| `src/data_analysis_agent/sampling/result_store.py` | Create | 持久化结果存储 + 按行回取 + 回收              |
| `src/data_analysis_agent/tools/retrieve_result.py` | Create | `retrieve_result` 工具                        |
| `src/data_analysis_agent/sampling/config.py`       | Modify | 加两个门控比率字段                            |
| `src/data_analysis_agent/sampling/text_summary.py` | Modify | `compact_result` 加 `context_pressure` + 门控 |
| `src/data_analysis_agent/sampling/__init__.py`     | Modify | 导出 `ResultStore` / `RetrievedPage`          |
| `src/data_analysis_agent/agent_loop.py`            | Modify | 持有 store、`_context_pressure`、接线 marker  |
| `src/data_analysis_agent/config.py`                | Modify | store 配置 + 工厂                             |
| `src/data_analysis_agent/__main__.py`              | Modify | 装配 store + 注册工具                         |
| `docs/ARCHITECTURE.md`                             | Modify | manifest 登记 2 新模块                        |
| `docs/adr/0003-ccr-lite-result-retrieval.md`       | Create | 决策记录                                      |
| `tests/test_result_store.py`                       | Create | ResultStore 测试                              |
| `tests/test_retrieve_tool.py`                      | Create | retrieve 工具测试                             |
| `tests/test_sampling.py`                           | Modify | 门控测试                                      |
| `tests/test_ccr_wiring.py`                         | Create | agent_loop 接线测试                           |

注:`sampling/result_store.py` 必须**零包内 import**(纯 stdlib),以满足依赖规则(sampling 是叶子)。

---

## Task 1: 持久化 ResultStore(TDD)

**Files:**

- Create: `src/data_analysis_agent/sampling/result_store.py`
- Test: `tests/test_result_store.py`

- [ ] **Step 1: 写失败测试 `tests/test_result_store.py`**

```python
"""Tests for the persistent CCR-lite result store."""

from __future__ import annotations

from data_analysis_agent.sampling.result_store import ResultStore


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _store(tmp_path, **kw):
    return ResultStore(tmp_path / "results", clock=_Clock(), **kw)


def test_put_get_roundtrip_with_header(tmp_path):
    store = _store(tmp_path)
    content = "\n".join(f"row{i}" for i in range(100))
    assert store.put("t1", content, {"tool": "big"}) is True
    page = store.get("t1", offset=0, limit=5)
    assert page is not None
    assert page.total_lines == 100
    assert "row0" in page.text and "row4" in page.text
    assert "row5" not in page.text.split("\n", 1)[1]  # not in body (header may differ)
    assert "result_id=t1" in page.text
    assert page.tool == "big"


def test_pagination_offset_limit(tmp_path):
    store = _store(tmp_path)
    store.put("t1", "\n".join(str(i) for i in range(100)), {})
    page = store.get("t1", offset=10, limit=3)
    assert page is not None
    body = page.text.split("\n", 1)[1]
    assert body.splitlines() == ["10", "11", "12"]


def test_query_filter_case_insensitive(tmp_path):
    store = _store(tmp_path)
    store.put("t1", "Alpha\nBETA\nalphabet\ngamma", {})
    page = store.get("t1", query="alpha", limit=50)
    assert page is not None
    assert page.matched_lines == 2  # "Alpha" and "alphabet"
    body = page.text.split("\n", 1)[1]
    assert "Alpha" in body and "alphabet" in body and "gamma" not in body


def test_ttl_expiry(tmp_path):
    clock = _Clock()
    store = ResultStore(tmp_path / "r", ttl_seconds=100, clock=clock)
    store.put("t1", "data", {})
    clock.t += 101
    assert store.get("t1") is None


def test_total_size_eviction_oldest_first(tmp_path):
    clock = _Clock()
    store = ResultStore(tmp_path / "r", max_total_bytes=30, clock=clock)
    store.put("old", "x" * 20, {})
    clock.t += 1
    store.put("new", "y" * 20, {})  # total 40 > 30 -> evict oldest "old"
    assert store.get("old") is None
    assert store.get("new") is not None


def test_oversized_entry_not_stored(tmp_path):
    store = ResultStore(tmp_path / "r", max_entry_bytes=10, clock=_Clock())
    assert store.put("t1", "x" * 50, {}) is False
    assert store.get("t1") is None


def test_resume_from_disk(tmp_path):
    d = tmp_path / "r"
    s1 = ResultStore(d, clock=_Clock())
    s1.put("t1", "hello\nworld", {"tool": "big"})
    s2 = ResultStore(d, clock=_Clock())  # fresh instance, same dir
    page = s2.get("t1")
    assert page is not None and "hello" in page.text


def test_weird_id_is_filename_safe(tmp_path):
    store = _store(tmp_path)
    rid = "toolu_01/../weird:id"
    assert store.put(rid, "data", {}) is True
    assert store.get(rid) is not None


def test_page_byte_cap(tmp_path):
    store = _store(tmp_path)
    store.put("t1", "\n".join("x" * 200 for _ in range(200)), {})
    page = store.get("t1", offset=0, limit=200)
    assert page is not None
    assert page.truncated is True
    assert len(page.text) < 8000  # stays under trigger_chars so it isn't re-summarized
```

- [ ] **Step 2: 运行,确认失败**

Run: `.venv/bin/python -m pytest tests/test_result_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'data_analysis_agent.sampling.result_store'`

- [ ] **Step 3: 实现 `src/data_analysis_agent/sampling/result_store.py`**

```python
"""Persistent, line-paginated store for original tool results (CCR-lite).

Pure stdlib LEAF module — no imports from data_analysis_agent — so it stays
importable by both the harness (agent_loop) and the retrieve tool without
violating the sampling dependency rule. Holds the ORIGINAL content of a tool
result that was lossily compacted, so the model can retrieve it on demand.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Page text is capped below SamplingConfig.trigger_chars (8000) so a retrieved
# page is never itself re-summarized by compact_result at the tool-result seam.
_MAX_PAGE_CHARS = 7500


@dataclass
class RetrievedPage:
    """One page of a retrieved original result."""

    result_id: str
    total_lines: int
    matched_lines: int
    offset: int
    returned_lines: int
    text: str
    truncated: bool
    tool: str


class ResultStore:
    """Disk-backed store of original tool-result content, keyed by result id."""

    def __init__(
        self,
        store_dir: Path,
        *,
        ttl_seconds: int = 3600,
        max_total_bytes: int = 64 * 1024 * 1024,
        max_entry_bytes: int = 8 * 1024 * 1024,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.dir = Path(store_dir)
        self.ttl_seconds = ttl_seconds
        self.max_total_bytes = max_total_bytes
        self.max_entry_bytes = max_entry_bytes
        self._clock = clock
        self._index: dict[str, dict[str, Any]] = {}
        self._available = True
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._load_index()
            self._evict()
        except OSError:
            self._available = False  # read-only fs -> disabled, degrade gracefully

    @property
    def index_path(self) -> Path:
        return self.dir / "index.jsonl"

    def _file_for(self, result_id: str) -> Path:
        name = hashlib.sha256(result_id.encode("utf-8")).hexdigest()[:32]
        return self.dir / f"{name}.txt"

    def _load_index(self) -> None:
        if not self.index_path.exists():
            return
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            rid = rec.get("id")
            if isinstance(rid, str):
                self._index[rid] = rec  # last write wins

    def _rewrite_index(self) -> None:
        with self.index_path.open("w", encoding="utf-8") as fh:
            for rec in self._index.values():
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _drop(self, result_id: str) -> None:
        rec = self._index.pop(result_id, None)
        if rec is not None:
            try:
                Path(rec["file"]).unlink(missing_ok=True)
            except OSError:
                pass

    def _evict(self) -> None:
        now = self._clock()
        for rid in [r for r, rec in self._index.items() if now - rec.get("created_at", 0) > self.ttl_seconds]:
            self._drop(rid)
        total = sum(int(rec.get("bytes", 0)) for rec in self._index.values())
        if total > self.max_total_bytes:
            for rid, rec in sorted(self._index.items(), key=lambda kv: kv[1].get("created_at", 0)):
                if total <= self.max_total_bytes:
                    break
                total -= int(rec.get("bytes", 0))
                self._drop(rid)
        self._rewrite_index()

    def put(self, result_id: str, content: str, meta: dict[str, Any]) -> bool:
        if not self._available:
            return False
        data = content.encode("utf-8")
        if len(data) > self.max_entry_bytes:
            return False
        path = self._file_for(result_id)
        try:
            path.write_text(content, encoding="utf-8")
        except OSError:
            return False
        self._index[result_id] = {
            "id": result_id,
            "file": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "lines": content.count("\n") + 1,
            "created_at": self._clock(),
            "tool": str(meta.get("tool", "")),
        }
        self._evict()
        return result_id in self._index

    def get(
        self,
        result_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        query: str | None = None,
    ) -> RetrievedPage | None:
        if not self._available:
            return None
        rec = self._index.get(result_id)
        if rec is None:
            return None
        if self._clock() - rec.get("created_at", 0) > self.ttl_seconds:
            self._drop(result_id)
            self._rewrite_index()
            return None
        try:
            content = Path(rec["file"]).read_text(encoding="utf-8")
        except OSError:
            return None

        lines = content.split("\n")
        total = len(lines)
        if query:
            needle = query.lower()
            lines = [ln for ln in lines if needle in ln.lower()]
        matched = len(lines)
        offset = max(0, offset)
        page_lines = lines[offset : offset + max(1, limit)]
        body = "\n".join(page_lines)
        truncated = False
        if len(body) > _MAX_PAGE_CHARS:
            body = body[:_MAX_PAGE_CHARS] + "\n…[页过大已截断,缩小 limit 或用 query]"
            truncated = True
        tool = str(rec.get("tool", ""))
        query_note = f" (query={query!r} matched {matched})" if query else ""
        header = (
            f"[result_id={result_id} | lines {offset}-{offset + len(page_lines)} "
            f"of {total}{query_note} | tool={tool}]"
        )
        return RetrievedPage(
            result_id=result_id,
            total_lines=total,
            matched_lines=matched,
            offset=offset,
            returned_lines=len(page_lines),
            text=header + "\n" + body,
            truncated=truncated,
            tool=tool,
        )
```

- [ ] **Step 4: 运行,确认通过 + 格式/类型**

Run: `.venv/bin/python -m pytest tests/test_result_store.py -q`
Expected: PASS(9 测试)
Run: `.venv/bin/ruff format src tests && .venv/bin/ruff check src tests && .venv/bin/mypy src`
Expected: 全 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/data_analysis_agent/sampling/result_store.py tests/test_result_store.py
git commit -q -m "feat: add persistent CCR-lite ResultStore with line retrieval"
```

---

## Task 2: pressure-adaptive 收益门控(TDD)

**Files:**

- Modify: `src/data_analysis_agent/sampling/config.py`
- Modify: `src/data_analysis_agent/sampling/text_summary.py`
- Test: `tests/test_sampling.py`

- [ ] **Step 1: 加配置字段** — 在 `SamplingConfig` 的 `trigger_rows: int = 50` 之后追加:

```python
    gate_ratio_low_pressure: float = 0.65
    gate_ratio_high_pressure: float = 0.90
```

- [ ] **Step 2: 写失败测试** — 在 `tests/test_sampling.py` 末尾追加:

```python
def test_gating_passthrough_low_gain_low_pressure():
    # A result just over trigger whose digest is not <=65% of original -> passthrough.
    config = SamplingConfig(trigger_chars=50)
    content = "uniquelinexyz" * 8  # 104 chars, no newlines/structure -> weak digest
    out, was = compact_result(content, max_chars=50_000, config=config, context_pressure=0.0)
    assert was is False
    assert out == content


def test_gating_compresses_under_high_pressure():
    config = SamplingConfig(trigger_chars=50)
    content = "\n".join(f"k{i}=v{i}" for i in range(40))  # ~ a few hundred chars
    out_low, was_low = compact_result(content, 50_000, config, context_pressure=0.0)
    out_high, was_high = compact_result(content, 50_000, config, context_pressure=1.0)
    # high pressure is at least as willing to compress as low pressure
    assert was_high or (was_low == was_high)


def test_gating_forces_compression_over_max_chars():
    config = SamplingConfig(trigger_chars=50)
    content = "x" * 5000  # one line, no structure
    out, was = compact_result(content, max_chars=1000, config=config, context_pressure=0.0)
    assert was is True
    assert len(out) <= 1000


def test_compact_result_default_pressure_is_zero():
    # backward-compatible call without context_pressure still works
    out, was = compact_result("small", max_chars=50_000)
    assert (out, was) == ("small", False)
```

- [ ] **Step 3: 运行,确认新测试失败**

Run: `.venv/bin/python -m pytest tests/test_sampling.py -q -k gating`
Expected: FAIL(`compact_result` 暂不接受 `context_pressure` / 无门控)。

- [ ] **Step 4: 改 `compact_result`** — 替换 `text_summary.py` 中现有 `compact_result` 函数为:

```python
def compact_result(
    content: str,
    max_chars: int,
    config: SamplingConfig | None = None,
    context_pressure: float = 0.0,
) -> tuple[str, bool]:
    """Compact an oversized tool result with pressure-adaptive gain gating.

    Returns ``(content, was_compacted)``. Results at or below
    ``config.trigger_chars`` pass through untouched. After summarizing, the
    summary replaces the original only if it is short enough relative to an
    acceptance ratio that scales with ``context_pressure`` (0=empty→strict,
    1=near full→lenient), unless the original exceeds ``max_chars`` (which would
    otherwise be truncated), in which case compaction is forced.
    """
    config = config or SamplingConfig()
    if len(content) <= config.trigger_chars:
        return content, False
    try:
        out = summarize_text(content, config)
    except Exception:
        out = _head_tail_truncate(content, config.trigger_chars)

    pressure = min(1.0, max(0.0, context_pressure))
    accept_ratio = config.gate_ratio_low_pressure + (
        config.gate_ratio_high_pressure - config.gate_ratio_low_pressure
    ) * pressure
    fits_within_cap = len(content) <= max_chars
    if len(out) > len(content) * accept_ratio and fits_within_cap:
        return content, False  # gain too small and original fits -> keep original

    if max_chars and len(out) > max_chars:
        out = _head_tail_truncate(out, max_chars)
    return out, True
```

- [ ] **Step 5: 运行全套件,确认通过且无回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全绿(原 72 + 9 store + 4 门控 = 85 passed;若有原测试因门控翻转,核对该测试输入并调整断言或确认行为正确)。
Run: `.venv/bin/ruff check src tests && .venv/bin/mypy src`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/data_analysis_agent/sampling/config.py src/data_analysis_agent/sampling/text_summary.py tests/test_sampling.py
git commit -q -m "feat: pressure-adaptive compression-gain gating in compact_result"
```

---

## Task 3: retrieve_result 工具(TDD)

**Files:**

- Create: `src/data_analysis_agent/tools/retrieve_result.py`
- Modify: `src/data_analysis_agent/sampling/__init__.py`(导出 ResultStore/RetrievedPage)
- Test: `tests/test_retrieve_tool.py`

- [ ] **Step 1: 导出 store 类型** — 在 `sampling/__init__.py` 增加导入与 `__all__` 项:

```python
from .result_store import ResultStore, RetrievedPage
```

并在 `__all__` 列表中加入 `"ResultStore"` 和 `"RetrievedPage"`(保持字母序不强制,但需出现)。

- [ ] **Step 2: 写失败测试 `tests/test_retrieve_tool.py`**

```python
"""Tests for the retrieve_result tool."""

from __future__ import annotations

import pytest

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
```

- [ ] **Step 3: 运行,确认失败**

Run: `.venv/bin/python -m pytest tests/test_retrieve_tool.py -q`
Expected: FAIL — `ModuleNotFoundError: ... tools.retrieve_result`

- [ ] **Step 4: 实现 `src/data_analysis_agent/tools/retrieve_result.py`**

```python
"""RetrieveResultTool: page through the original of a summarized tool result."""

from __future__ import annotations

from typing import Any

from ..sampling.result_store import ResultStore
from .base import CanUseToolFn, Tool, ToolResult, ValidationResult


class RetrieveResultTool(Tool):
    """Return the full original content of a previously summarized tool result."""

    def __init__(self, result_store: ResultStore | None = None) -> None:
        self.result_store = result_store

    @property
    def name(self) -> str:
        return "retrieve_result"

    @property
    def description(self) -> str:
        return (
            "Retrieve the full original content of a previously summarized tool result. "
            "Large tool results are summarized in context and tagged with a result_id; "
            "page through the original by line via offset/limit, optionally filtering with "
            "a case-insensitive query substring. For exact aggregates (sum/count/ratio), "
            "recompute in pandas via python_analysis instead of reading raw rows."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "result_id": {
                    "type": "string",
                    "description": "id from the '[完整结果已缓存...]' retrieval marker",
                },
                "offset": {"type": "integer", "description": "0-based starting line (default 0)"},
                "limit": {"type": "integer", "description": "max lines to return, 1-500 (default 50)"},
                "query": {"type": "string", "description": "optional case-insensitive substring filter"},
            },
            "required": ["result_id"],
        }

    def is_concurrency_safe(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        if not input_data.get("result_id"):
            return ValidationResult.fail("result_id is required")
        offset = input_data.get("offset", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            return ValidationResult.fail("offset must be a non-negative integer")
        limit = input_data.get("limit", 50)
        if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
            return ValidationResult.fail("limit must be an integer in 1..500")
        return ValidationResult.success()

    async def call(
        self,
        input_data: dict[str, Any],
        can_use_tool: CanUseToolFn | None = None,
    ) -> ToolResult:
        if self.result_store is None:
            return ToolResult(
                content="Result retrieval is not available in this session.",
                is_error=True,
            )
        page = self.result_store.get(
            str(input_data["result_id"]),
            offset=int(input_data.get("offset", 0)),
            limit=int(input_data.get("limit", 50)),
            query=input_data.get("query"),
        )
        if page is None:
            return ToolResult(
                content=(
                    f"result_id '{input_data['result_id']}' not found or expired (TTL=1h). "
                    "Recompute with python_analysis if needed."
                ),
                is_error=True,
            )
        return ToolResult(content=page.text)
```

- [ ] **Step 5: 运行,确认通过**

Run: `.venv/bin/python -m pytest tests/test_retrieve_tool.py -q`
Expected: PASS(8 测试)
Run: `.venv/bin/ruff format src tests && .venv/bin/ruff check src tests && .venv/bin/mypy src`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/data_analysis_agent/tools/retrieve_result.py src/data_analysis_agent/sampling/__init__.py tests/test_retrieve_tool.py
git commit -q -m "feat: add retrieve_result tool for paginated result retrieval"
```

---

## Task 4: agent_loop 接线 + 配置 + 装配(TDD)

**Files:**

- Modify: `src/data_analysis_agent/agent_loop.py`
- Modify: `src/data_analysis_agent/config.py`
- Modify: `src/data_analysis_agent/__main__.py`
- Test: `tests/test_ccr_wiring.py`

- [ ] **Step 1: 写失败测试 `tests/test_ccr_wiring.py`**

```python
"""Integration test: agent_loop stores originals and injects retrieval markers."""

from __future__ import annotations

from typing import Any

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.protocol.messages import ToolUseBlock
from data_analysis_agent.sampling import SamplingConfig
from data_analysis_agent.sampling.result_store import ResultStore
from data_analysis_agent.state_machine import AgentState, Message
from data_analysis_agent.tools.base import CanUseToolFn, Tool, ToolResult, ValidationResult
from data_analysis_agent.tools.registry import ToolRegistry


class _BigTool(Tool):
    @property
    def name(self) -> str:
        return "big"

    @property
    def description(self) -> str:
        return "emits a large result"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def is_read_only(self, input_data: dict[str, Any]) -> bool:
        return True

    def is_destructive(self, input_data: dict[str, Any]) -> bool:
        return False

    async def call(
        self, input_data: dict[str, Any], can_use_tool: CanUseToolFn | None = None
    ) -> ToolResult:
        return ToolResult(content="col\n" + "\n".join(f"row{i}" for i in range(2000)))


def _agent(store):
    registry = ToolRegistry()
    registry.register(_BigTool())
    return AgentLoop(
        AgentLoopConfig(api_key="x", model="m"),
        registry,
        result_store=store,
        sampling_config=SamplingConfig(trigger_chars=200),
    )


def test_context_pressure_ratio():
    agent = _agent(None)
    agent.compressor.budget_tokens = 1000
    msgs = [Message(role="user", content="x" * 2000)]  # ~500 tokens
    p = agent._context_pressure(msgs)
    assert 0.0 <= p <= 1.0
    assert p > 0.4


async def test_large_result_stored_and_marked(tmp_path):
    store = ResultStore(tmp_path / "r")
    agent = _agent(store)
    state = AgentState(messages=[Message(role="user", content="hi")])
    blocks = [ToolUseBlock(id="call_1", name="big", input={})]
    results = await agent._execute_tools(blocks, state)
    assert "retrieve_result" in results[0].content
    page = store.get("call_1")
    assert page is not None
    assert "row1999" in page.text  # full original retrievable


async def test_small_result_not_stored(tmp_path):
    store = ResultStore(tmp_path / "r")
    registry = ToolRegistry()

    class _SmallTool(_BigTool):
        async def call(self, input_data, can_use_tool=None):
            return ToolResult(content="tiny")

    registry.register(_SmallTool())
    agent = AgentLoop(
        AgentLoopConfig(api_key="x", model="m"),
        registry,
        result_store=store,
        sampling_config=SamplingConfig(trigger_chars=200),
    )
    state = AgentState(messages=[Message(role="user", content="hi")])
    results = await agent._execute_tools([ToolUseBlock(id="c2", name="big", input={})], state)
    assert "retrieve_result" not in results[0].content
    assert store.get("c2") is None
```

- [ ] **Step 2: 运行,确认失败**

Run: `.venv/bin/python -m pytest tests/test_ccr_wiring.py -q`
Expected: FAIL(`AgentLoop` 无 `result_store` 参数 / 无 `_context_pressure` / 无 marker)。

- [ ] **Step 3: 改 `agent_loop.py` 导入** — 把
      `from .context.compression import ContextCompressor`
      改为:

```python
from .context.compression import ContextCompressor, estimate_tokens, message_to_text
```

并在文件已有的 `from .sampling import SamplingConfig, compact_result` 之后追加:

```python
from .sampling.result_store import ResultStore
```

- [ ] **Step 4: 改 `AgentLoop.__init__`** — 在签名 `sampling_config: SamplingConfig | None = None,` 之后加参数 `result_store: ResultStore | None = None,`;在 `self.sampling_config = sampling_config or SamplingConfig()` 之后加:

```python
        self.result_store = result_store
```

- [ ] **Step 5: 加 `_context_pressure` 方法** — 在 `AgentLoop` 内 `_execute_tools` 方法定义之前插入:

```python
    def _context_pressure(self, messages: list[Message]) -> float:
        """Fraction of the token budget currently used (clamped to [0, 1])."""
        budget = self.compressor.budget_tokens or 1
        total = sum(estimate_tokens(message_to_text(m)) for m in messages)
        return min(1.0, max(0.0, total / budget))
```

- [ ] **Step 6: 改 `_execute_tools` 压缩接缝** — 把现有:

```python
                tool_result: ToolResult = await tool.call(block.input)
                content, _ = compact_result(
                    tool_result.content,
                    tool.max_result_size_chars,
                    self.sampling_config,
                )
```

替换为:

```python
                tool_result: ToolResult = await tool.call(block.input)
                pressure = self._context_pressure(state.messages)
                content, was_compacted = compact_result(
                    tool_result.content,
                    tool.max_result_size_chars,
                    self.sampling_config,
                    pressure,
                )
                if was_compacted and self.result_store is not None:
                    stored = self.result_store.put(
                        block.id, tool_result.content, {"tool": block.name}
                    )
                    if stored:
                        content += (
                            '\n\n[完整结果已缓存。回取: retrieve_result('
                            f'result_id="{block.id}", offset=0, limit=50)]'
                        )
```

- [ ] **Step 7: 改 `config.py`** — 在 `AgentConfig` 的 `sampling_fidelity: str = "mid"` 之后加字段与工厂:

```python
    # Result store (CCR-lite)
    result_store_ttl_seconds: int = 3600
    result_store_max_total_mb: int = 64
    result_store_max_entry_mb: int = 8

    def result_store(self, persist_path: str | Path | None = None) -> Any:
        """Build a ResultStore; dir follows persist_path (else a tempdir)."""
        import tempfile

        from .sampling.result_store import ResultStore

        if persist_path:
            store_dir = Path(persist_path).expanduser().resolve().parent / "results"
        else:
            store_dir = Path(tempfile.mkdtemp(prefix="daa_results_"))
        return ResultStore(
            store_dir,
            ttl_seconds=self.result_store_ttl_seconds,
            max_total_bytes=self.result_store_max_total_mb * 1024 * 1024,
            max_entry_bytes=self.result_store_max_entry_mb * 1024 * 1024,
        )
```

- [ ] **Step 8: 改 `__main__.py` 装配** — 在 `build_registry` 中,把签名改为
      `def build_registry(config: AgentConfig | None = None, result_store: Any = None) -> ToolRegistry:`,
      在 `registry.register(VisualizationTool())` 之后加:

```python
    from .tools.retrieve_result import RetrieveResultTool

    registry.register(RetrieveResultTool(result_store=result_store))
```

然后在 `run_agent` 中,`registry = build_registry(config)` 改为:

```python
    result_store = config.result_store(persist_path)
    registry = build_registry(config, result_store=result_store)
```

并把 `AgentLoop(...)` 调用里加入 `result_store=result_store,`(与 `sampling_config=config.sampling_config(),` 并列)。

- [ ] **Step 9: 运行接线测试 + 全套件**

Run: `.venv/bin/python -m pytest tests/test_ccr_wiring.py -q`
Expected: PASS(3 测试)
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全绿(约 88 passed)
Run: `.venv/bin/ruff format src tests && .venv/bin/ruff check src tests && .venv/bin/mypy src`
Expected: PASS。

- [ ] **Step 10: 提交**

```bash
git add src/data_analysis_agent/agent_loop.py src/data_analysis_agent/config.py src/data_analysis_agent/__main__.py tests/test_ccr_wiring.py
git commit -q -m "feat: wire ResultStore + retrieval markers into agent loop"
```

---

## Task 5: manifest + ADR + 质量闸收口

**Files:**

- Modify: `docs/ARCHITECTURE.md`
- Create: `docs/adr/0003-ccr-lite-result-retrieval.md`

- [ ] **Step 1: 登记 manifest** — 在 `docs/ARCHITECTURE.md` 的 manifest 段(`<!-- manifest:start -->`…）内、`sampling/sandbox_summary.py` 行之后加一行;并在 tools 区 `tools/visualization.py` 行之后加一行:

```
src/data_analysis_agent/sampling/result_store.py = "持久化结果存储(CCR-lite):原文落盘 + 按行回取 + TTL/容量回收"
src/data_analysis_agent/tools/retrieve_result.py = "retrieve_result 工具:按行分页回取被摘要前的原始工具结果"
```

- [ ] **Step 2: 写 ADR `docs/adr/0003-ccr-lite-result-retrieval.md`**

```markdown
# 0003 — CCR-lite:大工具结果可回取 + pressure-adaptive 收益门控

- 状态: Accepted (2026-06-08)

## 背景

sampling 对大结果有损摘要后原文不可回取(采样不可逆风险);compact_result 无收益门控。
调研 headroom(`research/headroom/`)的 CCR(Compress-Cache-Retrieve)与 context-pressure 门控。

## 决策

内化 CCR 思想为本项目自有层:持久化 `sampling/result_store.py`(纯 stdlib 叶子)存原文 +
`tools/retrieve_result.py` 按行分页回取(offset/limit/query,比 headroom 仅 hash+query 多了分页,
防 token 反弹);`compact_result` 增 context_pressure 自适应门控(空闲严 0.65 ↔ 接近满松 0.90)。
**不引入 headroom 本体、proxy、ML 压缩、第三方 sketch 库**(与 ADR 0001 一致)。

## 理由

原文可回取消除采样不可逆风险;持久化跨 resume/fork 可回取;门控避免"越压越长信息更少"。
确定性、纯 stdlib、可测,契合项目精简依赖与防熵规范。

## 影响

新增 `sampling/result_store.py`、`tools/retrieve_result.py`;agent_loop 接线;TTL+容量上限防膨胀。
详见 `docs/superpowers/specs/2026-06-08-ccr-lite-result-retrieval-design.md`。
```

- [ ] **Step 3: 跑完整质量闸**

Run: `.venv/bin/python scripts/quality_gate.py`
Expected: 五步全 `[PASS]`(含 drift:manifest 已同步、依赖规则 0 违例、无死链)。若 manifest 不匹配则补齐。

- [ ] **Step 4: 提交**

```bash
git add docs/ARCHITECTURE.md docs/adr/0003-ccr-lite-result-retrieval.md
git commit -q -m "docs: register CCR-lite modules in manifest, add ADR 0003"
```

- [ ] **Step 5: 端到端实测(实践检验)**

Run:

```bash
.venv/bin/python - <<'PY'
import asyncio
from data_analysis_agent.sampling.result_store import ResultStore
from data_analysis_agent.tools.retrieve_result import RetrieveResultTool
import tempfile, pathlib
store = ResultStore(pathlib.Path(tempfile.mkdtemp())/"r")
store.put("call_x", "\n".join(f"order_{i},amount={i*7}" for i in range(500)), {"tool":"nl_query"})
tool = RetrieveResultTool(store)
print(asyncio.run(tool.call({"result_id":"call_x","query":"amount=70","limit":5})).content)
PY
```

Expected: 打印含 `result_id=call_x`、按 query 过滤的若干行(如 `order_10,amount=70`)。

---

## Self-Review(写计划者已自查)

- **Spec coverage**:§5 ResultStore(Task 1)、§6 门控(Task 2)、§7 retrieve 工具(Task 3)、
  §8 接线 + §9 配置(Task 4)、§12 manifest/ADR(Task 5)、§11 测试(各 Task 的 test 文件)、
  §13 验收(Task 4 Step 9 + Task 5 Step 3/5)——均有任务承载。
- **Placeholder scan**:无 TBD/TODO;所有代码、命令、预期输出均给全。
- **类型/签名一致性**:`ResultStore(store_dir, *, ttl_seconds, max_total_bytes, max_entry_bytes, clock)`、
  `put(result_id, content, meta)->bool`、`get(result_id,*,offset,limit,query)->RetrievedPage|None`、
  `RetrievedPage(result_id,total_lines,matched_lines,offset,returned_lines,text,truncated,tool)`、
  `compact_result(content,max_chars,config,context_pressure)->tuple[str,bool]`、
  `RetrieveResultTool(result_store)`、`AgentLoop(..., result_store=None)`、`_context_pressure(messages)->float`
  —— 各 Task 引用处一致。

```

```
