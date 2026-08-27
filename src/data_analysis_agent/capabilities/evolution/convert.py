"""v2 TrajectoryEvent → v1 TurnRecord 转换器(离线进化管线的兼容桥).

既有 ``evolution/`` 管线(synthesizer / evaluator)消费 v1 ``telemetry`` 的
TurnRecord 字典;本模块把 v2 事件流折回 TurnRecord,使离线管线对 v2 轨迹
**零改动**可用。映射原则仍是「记结构不记数值」:v1 里本应放原文的字段
(``user_input`` / ``final_text_digest``)一律写入 digest 占位符
``"(digest-only: <digest>)"``,绝不凭空补值。

字段映射(详见各函数注释):
* ``turn_id`` — ``sha256(session_id|turn)[:12]``,确定性、可重放;
* ``model_turns`` — model_input 事件计数(v1 语义:工具迭代数);
* ``terminal_reason`` — outcome → v1 词汇表(complete→COMPLETED 等),
  COMPLETED 保住 synthesizer 的 is_eligible 判定;
* ``tokens`` — v2 契约无 token 事件,写 0/0 且 ``estimated: True``(下游
  成本分析按 v1 约定不信任精确值);
* ``tool_calls`` — tool_result 落记录(与 v1 在 ToolResultEvent 时落记录
  同步),配对前方最近的 tool_call 取 input_digest 与时长;
* ``active_skill`` — 最后一次 source="skill" 的 context_injection 的可选
  ``name`` 键(纯结构,无名字则 None)。
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from data_analysis_agent.telemetry.trajectory import ToolCallRecord, TurnRecord

from .trajectory import TrajectoryEvent

_OUTCOME_TO_TERMINAL = {
    "complete": "COMPLETED",
    "error": "MODEL_ERROR",
    "interrupted": "INTERRUPTED",
}


def _placeholder(digest_value: Any) -> str:
    """v1 文本字段的 digest 占位符;空值保持空串(v1 敏感模式同款)。"""

    if isinstance(digest_value, str) and digest_value:
        return f"(digest-only: {digest_value})"
    return ""


def _parse_ts(ts: str) -> datetime | None:
    """ISO-8601 UTC(带 Z)→ aware datetime;解析失败返回 None。"""

    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _turn_id(session_id: str, turn: int) -> str:
    return hashlib.sha256(f"{session_id}|{turn}".encode()).hexdigest()[:12]


def _convert_turn(session_id: str, turn: int, events: list[TrajectoryEvent]) -> TurnRecord | None:
    """单个 turn 的事件组 → TurnRecord;缺 turn_start/turn_end 则返回 None。"""

    starts = [e for e in events if e.event == "turn_start"]
    ends = [e for e in events if e.event == "turn_end"]
    if not starts or not ends:
        return None
    start, end = starts[0], ends[-1]

    tool_calls: list[ToolCallRecord] = []
    pending: list[tuple[str, str, str]] = []  # (call_ts, tool, input_digest)
    active_skill: str | None = None
    model_turns = 0
    for event in events:
        if event.event == "tool_call":
            pending.append(
                (_as_str(event.data.get("tool")), event.ts, _as_str(event.data.get("input_digest")))
            )
        elif event.event == "tool_result":
            if pending:
                tool, call_ts, input_digest = pending.pop(0)
            else:  # 适配层丢了 tool_call:仍落记录,时长 0、无输入摘要
                tool, call_ts, input_digest = _as_str(event.data.get("tool")), event.ts, ""
            duration_ms = 0
            began, finished = _parse_ts(call_ts), _parse_ts(event.ts)
            if began is not None and finished is not None:
                duration_ms = max(0, int((finished - began).total_seconds() * 1000))
            tool_calls.append(
                ToolCallRecord(
                    name=tool or _as_str(event.data.get("tool")),
                    is_error=not bool(event.data.get("ok", True)),
                    duration_ms=duration_ms,
                    result_chars=_as_int(event.data.get("chars")),
                    input_digest=input_digest,
                )
            )
        elif event.event == "model_input":
            model_turns += 1
        elif event.event == "context_injection" and event.data.get("source") == "skill":
            name = event.data.get("name")
            if isinstance(name, str) and name:
                active_skill = name

    tokens: dict[str, object] = {"input": 0, "output": 0, "estimated": True}
    return TurnRecord(
        session_id=session_id,
        turn_id=_turn_id(session_id, turn),
        ts_start=start.ts,
        ts_end=end.ts,
        user_input=_placeholder(start.data.get("user_input_digest")),
        active_skill=active_skill,
        tool_calls=tool_calls,
        terminal_reason=_OUTCOME_TO_TERMINAL.get(_as_str(end.data.get("outcome")), "UNKNOWN"),
        model_turns=model_turns,
        tokens=tokens,
        final_text_digest=_placeholder(end.data.get("final_output_digest")),
    )


def events_to_turn_records(session_id: str, events: list[TrajectoryEvent]) -> TurnRecord | None:
    """把一段事件流里第一个「完整 turn」(有 start 有 end)折成 TurnRecord。

    自动过滤其它 session 的事件;没有任何完整 turn 时返回 None(调用方以此
    判定轨迹不完整,而非靠异常)。
    """

    by_turn: dict[int, list[TrajectoryEvent]] = defaultdict(list)
    for event in events:
        if event.session_id == session_id:
            by_turn[event.turn].append(event)
    for turn in sorted(by_turn):
        record = _convert_turn(session_id, turn, by_turn[turn])
        if record is not None:
            return record
    return None


def load_v2_turns(path: Path) -> list[TurnRecord]:
    """读取一个 v2 轨迹 JSONL,按 (session, turn) 分组折成 TurnRecord 列表。

    离线、尽力而为:坏行 / 校验失败行 / 不完整 turn 一律跳过,绝不让单个
    损坏行毁掉整份语料(与 verify_file 的报告职责互补)。
    """

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    by_session: dict[str, dict[int, list[TrajectoryEvent]]] = defaultdict(lambda: defaultdict(list))
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        try:
            event = TrajectoryEvent.from_dict(raw)
        except ValueError:
            continue
        by_session[event.session_id][event.turn].append(event)
    records: list[TurnRecord] = []
    for session_id, by_turn in by_session.items():
        for turn in sorted(by_turn):
            record = _convert_turn(session_id, turn, by_turn[turn])
            if record is not None:
                records.append(record)
    return records


__all__ = ["events_to_turn_records", "load_v2_turns"]
