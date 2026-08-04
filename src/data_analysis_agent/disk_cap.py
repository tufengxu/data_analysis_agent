"""Best-effort directory disk-cap helper.

Shared by telemetry (trajectories) and the skills store: when a directory
exceeds a byte cap, evict eligible files — oldest / lowest-rank first — until
under the cap. Protected files (the current session, active skills, ...) are
never evicted but still count toward the total, so the cap reflects true disk
usage rather than a size that can be gamed by protecting everything.

Best-effort throughout: a cap check must never break the caller's flow on a
filesystem error (read-only fs, permission denied, vanished files between stat
and unlink). Mirrors the graceful-degradation contract of ``JsonlStore`` and the
trajectory logger — telemetry / evolution carriers are side channels and must
not take down the live loop.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any


def enforce_dir_disk_cap(
    directory: Path,
    max_bytes: int,
    *,
    pattern: str = "*",
    is_protected: Callable[[Path], bool] | None = None,
    eviction_rank: Callable[[Path], Any] | None = None,
) -> int:
    """Evict files under ``directory`` matching ``pattern`` until total size <=
    ``max_bytes``. Returns the number of files evicted. Never raises.

    ``total`` is the sum of sizes of ALL matched files (protected + evictable);
    a protected file still occupies budget, it just can't be removed to satisfy
    the cap. A file is evictable when ``is_protected(p)`` is falsy (default:
    nothing protected). Evictable files are removed in ascending ``eviction_rank``
    order — default mtime (oldest first) — until the total fits the cap.
    """
    try:
        sized: list[tuple[Path, int, float]] = []  # (path, size, mtime)
        total = 0
        for p in sorted(directory.glob(pattern)):
            with contextlib.suppress(OSError):
                st = p.stat()
                sized.append((p, st.st_size, st.st_mtime))
                total += st.st_size
        if total <= max_bytes:
            return 0

        protected = is_protected or (lambda _p: False)
        evictable = [(p, sz, mt) for p, sz, mt in sized if not protected(p)]
        if eviction_rank is None:
            evictable.sort(key=lambda t: t[2])  # mtime ascending → oldest first
        else:
            evictable.sort(key=lambda t: eviction_rank(t[0]))

        evicted = 0
        for p, sz, _mt in evictable:
            if total <= max_bytes:
                break
            try:
                p.unlink()
            except OSError:
                continue  # not removed — don't credit the count or total
            total -= sz
            evicted += 1
        return evicted
    except OSError:
        return 0
