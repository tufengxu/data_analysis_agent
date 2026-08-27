"""TrajectoryEvent contract (schema ``daa.trajectory.v1``) — harness 无关.

自进化的原材料契约:任意基座(v1 / Pi / dsh)的事件流由各自适配层翻译成
本契约的 JSON 事件。铁律是「记结构不记数值」(ADR 0004 同款纪律):内容
字段只携带 digest(``sha256[:12]:len``)或计数,绝不落原始文本 —— 所以
本模块无需 PII scrub(无值可泄)。

每类事件的 ``data`` 必填键(全部为结构信息):

===================  =========================================================
event                required data keys
===================  =========================================================
turn_start           ``user_input_digest`` (digest,非原文)
model_input          ``summary`` (dict,至少含 ``n_messages``/``n_tools`` 计数)
tool_call            ``tool`` (str), ``input_digest`` (digest)
tool_result          ``tool`` (str), ``ok`` (bool), ``output_digest``,
                     ``chars`` (int >= 0)
context_injection    ``source`` ("memory"|"skill"|"system" 等), ``chars``
turn_end             ``outcome`` ("complete"|"error"|"interrupted")
===================  =========================================================

校验对任意 ``*_digest`` 键强制 digest 形态(拒绝疑似原文),并对超长字符串
值按「疑似原文」拒绝 —— fail-closed 而非依赖调用方自觉。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: 事件契约 schema 标识(JSON 流级元数据,适配层按此对齐)。
TRAJECTORY_SCHEMA = "daa.trajectory.v1"

EVENT_TYPES = (
    "turn_start",
    "model_input",
    "tool_call",
    "tool_result",
    "context_injection",
    "turn_end",
)

#: 事件来源基座;适配层翻译时声明自己,离线侧可按基座分组分析。
HARNESSES = ("v1", "pi", "dsh")

#: turn_end.data["outcome"] 的封闭词表。
OUTCOMES = ("complete", "error", "interrupted")

DIGEST_RE = re.compile(r"^[0-9a-f]{12}:\d+$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

#: 结构字段里字符串值的上限;超过即视为「疑似原文」拒绝(记结构不记数值)。
_MAX_STRUCTURAL_STR = 256

_FIELDS = ("event", "ts", "session_id", "turn", "harness")


def digest(text: str) -> str:
    """内容字段的统一摘要:sha256 hex 前 12 位 + 字符长度。

    形如 ``"3b5d2f07a1c9:4096"`` —— 身份(哈希)+ 量级(长度),足够聚类
    与统计分析,不足以还原任何数值或文本。
    """

    return f"{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}:{len(text)}"


def _check_digest(value: Any) -> str | None:
    if isinstance(value, str) and DIGEST_RE.fullmatch(value):
        return None
    # repr 截断:诊断信息足以定位,又不把疑似原文整段倾倒进日志。
    return f"expected digest '<12-hex>:<chars>', got raw-looking value {repr(value)[:60]}"


def _check_nonempty_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return None
    return f"expected a non-empty string, got {value!r}"


def _check_bool(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    return f"expected a boolean, got {value!r}"


def _check_count(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return None
    return f"expected a non-negative integer, got {value!r}"


def _check_summary(value: Any) -> str | None:
    if not isinstance(value, dict):
        return f"expected an object with n_messages/n_tools counts, got {value!r}"
    for key in ("n_messages", "n_tools"):
        problem = _check_count(value.get(key))
        if problem:
            return f"summary[{key!r}]: {problem}"
    return None


def _check_outcome(value: Any) -> str | None:
    if isinstance(value, str) and value in OUTCOMES:
        return None
    return f"expected one of {OUTCOMES}, got {value!r}"


_DATA_RULES: dict[str, dict[str, Callable[[Any], str | None]]] = {
    "turn_start": {"user_input_digest": _check_digest},
    "model_input": {"summary": _check_summary},
    "tool_call": {"tool": _check_nonempty_str, "input_digest": _check_digest},
    "tool_result": {
        "tool": _check_nonempty_str,
        "ok": _check_bool,
        "output_digest": _check_digest,
        "chars": _check_count,
    },
    "context_injection": {"source": _check_nonempty_str, "chars": _check_count},
    "turn_end": {"outcome": _check_outcome},
}


def _get_str(d: dict[str, Any], key: str) -> str:
    """取字符串字段;类型不符按缺失处理(交给 validate 报问题)。"""

    value = d.get(key)
    return value if isinstance(value, str) else ""


def _walk_strings(obj: Any) -> list[str]:
    """收集 dict/list 树中的所有字符串值(长度哨兵用,不看键名)。"""

    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _walk_strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _walk_strings(v)]
    return []


@dataclass(frozen=True)
class TrajectoryEvent:
    """一条轨迹事件:公共信封 + 事件专属 ``data``(仅结构)。

    ``ts`` 为 ISO-8601 UTC(带 ``Z`` 后缀);``turn`` 从 0 起计;``harness``
    声明来源基座。``validate()`` 返回全部问题(空列表即合法),便于写入端
    一次性报告、校验端聚合展示 —— 永不靠异常携带部分信息。
    """

    event: str
    ts: str
    session_id: str
    turn: int
    harness: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON 友好视图(未知顶层键不参与 round-trip,契约容错向前)。"""

        return {
            "event": self.event,
            "ts": self.ts,
            "session_id": self.session_id,
            "turn": self.turn,
            "harness": self.harness,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TrajectoryEvent:
        """反序列化 + 强校验:任何问题汇总成一条 ``ValueError``。"""

        if not isinstance(d, dict):
            raise ValueError("TrajectoryEvent must be built from an object mapping")
        turn = d.get("turn", -1)
        if not isinstance(turn, int) or isinstance(turn, bool):
            turn = -1
        data = d.get("data", {})
        if not isinstance(data, dict):
            data = {}
        event = cls(
            event=_get_str(d, "event"),
            ts=_get_str(d, "ts"),
            session_id=_get_str(d, "session_id"),
            turn=turn,
            harness=_get_str(d, "harness"),
            data=data,
        )
        problems = [f"missing required field {key!r}" for key in _FIELDS if key not in d]
        problems.extend(event.validate())
        if problems:
            raise ValueError("invalid TrajectoryEvent: " + "; ".join(problems))
        return event

    def validate(self) -> list[str]:
        """返回全部问题字符串(空 = 合法);fail-closed,逐条可读。"""

        problems: list[str] = []
        if self.event not in EVENT_TYPES:
            problems.append(f"unknown event type: {self.event!r} (expected one of {EVENT_TYPES})")
        if self.harness not in HARNESSES:
            problems.append(f"unknown harness: {self.harness!r} (expected one of {HARNESSES})")
        if not isinstance(self.turn, int) or isinstance(self.turn, bool) or self.turn < 0:
            problems.append(f"turn must be a non-negative integer, got {self.turn!r}")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            problems.append("session_id must be a non-empty string")
        if not isinstance(self.ts, str) or _TS_RE.match(self.ts) is None:
            problems.append(f"ts must be ISO-8601 UTC with Z suffix, got {self.ts!r}")
        if not isinstance(self.data, dict):
            problems.append("data must be an object mapping")
            return problems
        for key, check in _DATA_RULES.get(self.event, {}).items():
            if key not in self.data:
                problems.append(f"data[{self.event}] missing required key {key!r}")
                continue
            problem = check(self.data[key])
            if problem:
                problems.append(f"data[{self.event}][{key!r}]: {problem}")
        # 「记结构不记数值」的两道通用闸门:任意 *_digest 键必须是 digest 形态,
        # 任意超长字符串值视为疑似原文(不论键名)拒绝。
        for key, value in self.data.items():
            if key.endswith("_digest"):
                problem = _check_digest(value)
                if problem:
                    problems.append(f"data[{self.event}][{key!r}]: {problem}")
            for text in _walk_strings(value):
                if len(text) > _MAX_STRUCTURAL_STR:
                    problems.append(
                        f"data[{self.event}][{key!r}]: string value of {len(text)} chars "
                        f"looks like raw content (max {_MAX_STRUCTURAL_STR})"
                    )
                    break
        return problems


__all__ = [
    "EVENT_TYPES",
    "HARNESSES",
    "OUTCOMES",
    "TRAJECTORY_SCHEMA",
    "TrajectoryEvent",
    "digest",
]
