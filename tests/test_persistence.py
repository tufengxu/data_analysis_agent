"""Tests for MessageStore: append/load, corrupt-line tolerance, atomic rewrite, fork."""

from data_analysis_agent.persistence import MessageStore
from data_analysis_agent.state_machine import Message


def test_append_and_load_roundtrip(tmp_path):
    store = MessageStore(tmp_path / "s.jsonl")
    store.append(Message(role="user", content="hi"))
    store.append(Message(role="assistant", content=[{"type": "text", "text": "ok"}]))
    loaded = store.load_all()
    assert [m.role for m in loaded] == ["user", "assistant"]
    assert loaded[1].content == [{"type": "text", "text": "ok"}]


def test_load_all_skips_corrupt_lines(tmp_path):
    """A truncated/garbage line must be skipped, not crash session recovery."""
    path = tmp_path / "s.jsonl"
    store = MessageStore(path)
    store.append(Message(role="user", content="good1"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")  # corrupt
        fh.write('{"role": "user"}\n')  # missing 'content' → KeyError path
    store.append(Message(role="user", content="good2"))

    loaded = store.load_all()
    assert [m.content for m in loaded] == ["good1", "good2"]  # corrupt rows dropped


def test_rewrite_is_atomic_replace(tmp_path):
    path = tmp_path / "s.jsonl"
    store = MessageStore(path)
    store.append(Message(role="user", content="old1"))
    store.append(Message(role="user", content="old2"))

    store.rewrite([Message(role="user", content="new")])
    assert [m.content for m in store.load_all()] == ["new"]
    assert not (tmp_path / "s.jsonl.tmp").exists()  # temp cleaned up by replace


def test_load_last_n_and_len(tmp_path):
    store = MessageStore(tmp_path / "s.jsonl")
    for i in range(5):
        store.append(Message(role="user", content=f"m{i}"))
    assert len(store) == 5
    assert [m.content for m in store.load_last_n(2)] == ["m3", "m4"]


def test_fork_copies_last_n(tmp_path):
    store = MessageStore(tmp_path / "s.jsonl")
    for i in range(3):
        store.append(Message(role="user", content=f"m{i}"))
    forked = store.fork(tmp_path / "fork.jsonl", last_n=2)
    assert [m.content for m in forked.load_all()] == ["m1", "m2"]


def test_load_missing_file_returns_empty(tmp_path):
    assert MessageStore(tmp_path / "nope.jsonl").load_all() == []
