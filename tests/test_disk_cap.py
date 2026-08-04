"""Tests for the shared directory disk-cap helper.

Behavioral contract: evict oldest-first by default, never evict protected files
(though they count toward the total), honor a custom eviction rank, and never
raise on a missing directory or a vanished file.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from data_analysis_agent.disk_cap import enforce_dir_disk_cap


def _write(p: Path, size: int, *, mtime_offset: float = 0.0) -> None:
    p.write_text("x" * size, encoding="utf-8")
    if mtime_offset:
        st = p.stat()
        os.utime(p, (st.st_atime, time.time() + mtime_offset))


def test_no_eviction_when_under_cap(tmp_path: Path):
    _write(tmp_path / "a.jsonl", 100)
    _write(tmp_path / "b.jsonl", 200)
    evicted = enforce_dir_disk_cap(tmp_path, max_bytes=1024, pattern="*.jsonl")
    assert evicted == 0
    assert (tmp_path / "a.jsonl").exists()
    assert (tmp_path / "b.jsonl").exists()


def test_evicts_oldest_first_by_mtime(tmp_path: Path):
    _write(tmp_path / "old.jsonl", 1024, mtime_offset=-100)
    _write(tmp_path / "new.jsonl", 1024, mtime_offset=0)
    # total 2048, cap 1024 → exactly one evicted, the oldest.
    evicted = enforce_dir_disk_cap(tmp_path, max_bytes=1024, pattern="*.jsonl")
    assert evicted == 1
    assert not (tmp_path / "old.jsonl").exists()
    assert (tmp_path / "new.jsonl").exists()


def test_protected_file_never_evicted_but_counts(tmp_path: Path):
    _write(tmp_path / "keep.jsonl", 1500, mtime_offset=-100)  # oldest, but protected
    _write(tmp_path / "evict.jsonl", 1500, mtime_offset=0)
    # total 3000, cap 1024. keep is protected → only evict.jsonl is removable.
    evicted = enforce_dir_disk_cap(
        tmp_path,
        max_bytes=1024,
        pattern="*.jsonl",
        is_protected=lambda p: p.name == "keep.jsonl",
    )
    assert evicted == 1
    assert (tmp_path / "keep.jsonl").exists()
    assert not (tmp_path / "evict.jsonl").exists()


def test_custom_eviction_rank_overrides_mtime(tmp_path: Path):
    _write(tmp_path / "a.json", 10, mtime_offset=-100)  # older
    _write(tmp_path / "b.json", 10, mtime_offset=0)
    # total 20, cap 10 → exactly one evicted. rank forces b first despite a older.
    evicted = enforce_dir_disk_cap(
        tmp_path,
        max_bytes=10,
        pattern="*.json",
        eviction_rank=lambda p: 0 if p.name == "b.json" else 1,
    )
    assert evicted == 1
    assert not (tmp_path / "b.json").exists()
    assert (tmp_path / "a.json").exists()


def test_missing_directory_is_noop(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    evicted = enforce_dir_disk_cap(missing, max_bytes=0, pattern="*.jsonl")
    assert evicted == 0


def test_non_matching_pattern_counts_zero(tmp_path: Path):
    _write(tmp_path / "a.txt", 9999)
    # *.jsonl matches nothing → total 0 → no eviction; .txt untouched.
    evicted = enforce_dir_disk_cap(tmp_path, max_bytes=10, pattern="*.jsonl")
    assert evicted == 0
    assert (tmp_path / "a.txt").exists()


def test_unlink_failure_not_counted(tmp_path: Path, monkeypatch):
    """A failed unlink (read-only fs, permission) is not credited: evicted stays
    0 and the file remains, so the caller is never told a file was removed when
    it wasn't."""
    _write(tmp_path / "a.jsonl", 1024)
    _write(tmp_path / "b.jsonl", 1024)

    def fail_unlink(self, *args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    evicted = enforce_dir_disk_cap(tmp_path, max_bytes=0, pattern="*.jsonl")
    assert evicted == 0
    assert (tmp_path / "a.jsonl").exists()  # unlink never succeeded
    assert (tmp_path / "b.jsonl").exists()
