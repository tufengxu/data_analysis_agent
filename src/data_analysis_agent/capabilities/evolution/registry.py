"""自进化能力域的注册入口:轨迹事件写入 + 轨迹文件校验.

两个能力都声明 READ_ONLY —— 它们只写 ``~/.daa`` 下的遥测旁路数据
(结构化事件,无用户工件),不触碰会话状态,与 v1 telemetry 侧信道的
定位一致。校验失败 → ``validation_error``(问题全量列出);文件不可读 →
``execution_error``(fail-closed)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import (
    CapabilityError,
    CapabilityHandler,
    CapabilityOutput,
    CapabilityRegistry,
    CapabilitySpec,
    OutputKind,
    Permission,
)
from .store import TrajectoryWriter, verify_file
from .trajectory import EVENT_TYPES, HARNESSES, TrajectoryEvent

_EVENT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["event", "ts", "session_id", "turn", "harness", "data"],
    "properties": {
        "event": {"type": "string", "enum": list(EVENT_TYPES)},
        "ts": {"type": "string", "description": "ISO-8601 UTC timestamp with Z suffix"},
        "session_id": {"type": "string"},
        "turn": {"type": "integer", "minimum": 0},
        "harness": {"type": "string", "enum": list(HARNESSES)},
        "data": {
            "type": "object",
            "description": (
                "event-specific structural payload: digests "
                "'<12-hex>:<chars>' and counts only, never raw content"
            ),
        },
    },
}


def register_all(registry: CapabilityRegistry, *, root: Path | None = None) -> list[str]:
    """注册本域全部能力,返回能力名列表。

    ``root`` 仅供测试注入临时目录;缺省时每次调用按当时的 ``DAA_HOME``/默认
    根目录构造 writer(不缓存,环境变更即时生效)。
    """

    async def _record_event(inputs: dict[str, Any]) -> CapabilityOutput:
        try:
            event = TrajectoryEvent.from_dict(inputs)
            path = TrajectoryWriter(root=root).write_event(event)
        except ValueError as exc:
            raise CapabilityError("validation_error", str(exc)) from None
        return CapabilityOutput(
            content=(
                f"recorded {event.event} event for session {event.session_id} "
                f"(turn {event.turn}, harness {event.harness})"
            ),
            data={"written": True, "session_id": event.session_id, "path": str(path)},
        )

    async def _verify_trajectory(inputs: dict[str, Any]) -> CapabilityOutput:
        raw = inputs.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise CapabilityError("validation_error", "path (non-empty string) is required")
        path = Path(raw).expanduser()
        if not path.is_file():
            raise CapabilityError(
                "execution_error", f"trajectory file not found or unreadable: {path}"
            )
        report = verify_file(path)
        summary = "ok" if report["ok"] else "problems found"
        return CapabilityOutput(
            content=f"trajectory verification: {summary} ({report['events']} valid events)",
            data=report,
        )

    specs: list[tuple[CapabilitySpec, CapabilityHandler]] = [
        (
            CapabilitySpec(
                name="evolution_record_event",
                description=(
                    "Append one daa.trajectory.v1 event (structure-only: digests and "
                    "counts, never raw content) to the session's v2 trajectory JSONL."
                ),
                input_schema=_EVENT_INPUT_SCHEMA,
                domain="evolution",
                output_kind=OutputKind.STRUCTURED,
                permission=Permission.READ_ONLY,
                error_codes=("validation_error", "execution_error"),
            ),
            _record_event,
        ),
        (
            CapabilitySpec(
                name="evolution_verify_trajectory",
                description=(
                    "Verify a v2 trajectory JSONL file line by line; returns "
                    "{ok, events, problems} — a bad line is reported, never crashes."
                ),
                input_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string", "description": "path to a .jsonl trajectory"}
                    },
                },
                domain="evolution",
                output_kind=OutputKind.STRUCTURED,
                permission=Permission.READ_ONLY,
                error_codes=("validation_error", "execution_error"),
            ),
            _verify_trajectory,
        ),
    ]
    for spec, handler in specs:
        registry.register(spec, handler)
    return [spec.name for spec, _ in specs]


__all__ = ["register_all"]
