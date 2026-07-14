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


def test_index_rewrite_is_atomic_on_failed_replace(tmp_path, monkeypatch):
    """Crash-safety: if the atomic replace fails, the on-disk index must stay
    at its last good state — never truncated to a half-written file (which
    would orphan every result_id)."""
    import os

    store = _store(tmp_path)
    assert store.put("r1", "hello\n", {"tool": "t"})
    index_path = tmp_path / "results" / "index.jsonl"
    snapshot = index_path.read_text(encoding="utf-8")

    def _fail(src, dst):
        raise OSError("simulated mid-replace crash")

    monkeypatch.setattr(os, "replace", _fail)
    # In-memory mutation + rewrite attempt; replace fails, so the rewrite is
    # absorbed and the real index file is left untouched.
    store._index["r2"] = store._index["r1"]
    store._rewrite_index()
    monkeypatch.undo()

    assert index_path.read_text(encoding="utf-8") == snapshot  # prior file intact
    assert not (tmp_path / "results" / "index.jsonl.tmp").exists()  # tmp cleaned up
    # And the store still serves the previously-written result.
    assert store.get("r1") is not None


def test_index_rewrite_no_tmp_left_after_success(tmp_path):
    """On a successful rewrite no .tmp file lingers and the index is complete."""
    store = _store(tmp_path)
    store.put("a", "1\n", {"tool": "t"})
    store.put("b", "2\n", {"tool": "t"})
    tmp = tmp_path / "results" / "index.jsonl.tmp"
    assert not tmp.exists()
    lines = (tmp_path / "results" / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # both records persisted
