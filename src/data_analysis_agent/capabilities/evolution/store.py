"""TrajectoryWriter: v2 轨迹事件的 append-only JSONL 落盘与整文件校验.

一个 session 一个 ``<root>/<session_id>.jsonl``,一行一条事件;根目录默认
``$DAA_HOME/trajectories/v2``(``DAA_HOME`` 缺省时为 ``~/.daa``,与 v1
config 的 DAA_HOME 语义一致)。写入前强制走 ``TrajectoryEvent.validate()``
(fail-closed),session_id 另做文件名安全检查,杜绝路径穿越。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .trajectory import TrajectoryEvent

#: session_id 直接拼进文件名:只放行无路径语义、长度有界的片段。
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def default_root() -> Path:
    """轨迹根目录:``$DAA_HOME/trajectories/v2``,缺省 ``~/.daa/trajectories/v2``。"""

    home = os.environ.get("DAA_HOME", str(Path.home() / ".daa"))
    return Path(home) / "trajectories" / "v2"


class TrajectoryWriter:
    """按 session 追加写入已校验的事件;无内部状态,可并发安全地按需构造。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def write_event(self, event: TrajectoryEvent) -> Path:
        """校验 → 追加一行 JSON → flush;返回写入的文件路径。

        非法事件(含不安全 session_id)抛 ``ValueError``,问题全量列出;
        I/O 失败自然上抛,由调用方(能力信封)统一 fail-closed。
        """

        problems = event.validate()
        if _SESSION_ID_RE.fullmatch(event.session_id) is None:
            problems.append(
                f"session_id {event.session_id!r} is not a safe file name fragment "
                f"(expected {_SESSION_ID_RE.pattern})"
            )
        if problems:
            raise ValueError(f"invalid TrajectoryEvent: {'; '.join(problems)}")
        path = self.path_for(event.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            handle.flush()
        return path


def verify_file(path: Path) -> dict[str, Any]:
    """整文件校验:逐行解析 + 逐事件强校验,坏行记为 problem 而非崩溃。

    返回 ``{"ok": bool, "events": int, "problems": [...]}``;``events`` 为
    校验通过的事件数。文件不可读时同样以报告形式返回(ok=False),不抛出。
    """

    problems: list[str] = []
    events = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "events": 0, "problems": [f"unreadable file: {exc}"]}
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {lineno}: not valid JSON ({exc.msg})")
            continue
        if not isinstance(raw, dict):
            problems.append(f"line {lineno}: not a JSON object")
            continue
        try:
            TrajectoryEvent.from_dict(raw)
        except ValueError as exc:
            problems.append(f"line {lineno}: {exc}")
            continue
        events += 1
    return {"ok": not problems, "events": events, "problems": problems}


__all__ = ["TrajectoryWriter", "default_root", "verify_file"]
