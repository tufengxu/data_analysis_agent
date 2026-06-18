"""Tests for the JsonlStore primitive — the shared persistence mechanism.

These edge cases (crash-mid-rewrite, read-only fs, corrupt lines) used to be
re-tested across every domain store; now they live against one test surface.
"""

from data_analysis_agent.jsonl_store import JsonlStore


def test_append_and_read_roundtrip(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    assert store.append({"a": 1}) is True
    assert store.extend([{"a": 2}, {"a": 3}]) is True
    assert [r["a"] for r in store.read()] == [1, 2, 3]


def test_rewrite_is_atomic_and_cleans_tmp(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    store.extend([{"n": 1}, {"n": 2}])
    assert store.rewrite([{"n": 9}]) is True
    assert [r["n"] for r in store.read()] == [9]
    assert not (tmp_path / "s.jsonl.tmp").exists()  # temp swapped, not left behind


def test_read_skips_corrupt_and_non_dict_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    store = JsonlStore(path)
    store.append({"good": 1})
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")  # corrupt
        fh.write("[1, 2, 3]\n")  # valid json but not a dict row
        fh.write("\n")  # blank
    store.append({"good": 2})
    assert [r["good"] for r in store.read()] == [1, 2]


def test_read_unreadable_file_degrades(tmp_path, monkeypatch):
    path = tmp_path / "s.jsonl"
    store = JsonlStore(path)
    store.append({"x": 1})

    def boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.open", boom)
    assert store.read() == []  # unreadable → empty, not a crash


def test_readonly_dir_disables_writes(tmp_path, monkeypatch):
    def boom_mkdir(*a, **k):
        raise OSError("read-only fs")

    monkeypatch.setattr("pathlib.Path.mkdir", boom_mkdir)
    store = JsonlStore(tmp_path / "sub" / "s.jsonl")
    assert store.available is False
    assert store.append({"a": 1}) is False  # write no-ops, never raises
    assert store.rewrite([{"a": 1}]) is False


def test_count_and_clear(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    assert store.count() == 0  # missing file
    store.extend([{"a": 1}, {"a": 2}])
    assert store.count() == 2
    store.clear()
    assert store.count() == 0 and not store.exists()


def test_no_ensure_parent_skips_mkdir(tmp_path):
    # Reading a non-existent file under an existing dir must not create anything.
    store = JsonlStore(tmp_path / "nope.jsonl", ensure_parent=False)
    assert store.read() == []
    assert store.available is True
