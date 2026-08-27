"""自进化能力域:TrajectoryEvent 契约、轨迹写入/校验、→ TurnRecord 转换器。

harness 无关的事件契约(``daa.trajectory.v1``);Pi 与 dsh 适配层各写一个事件流→
契约的翻译器。进化管线(合成 → 冻结 fixture A/B → promote/rollback)仍离线运行于
v1 ``evolution/``,绝不在交互主循环内运行;数据继续落 ``~/.daa/``。
"""

from __future__ import annotations

from .convert import events_to_turn_records, load_v2_turns
from .registry import register_all
from .store import TrajectoryWriter, default_root, verify_file
from .trajectory import (
    EVENT_TYPES,
    HARNESSES,
    OUTCOMES,
    TRAJECTORY_SCHEMA,
    TrajectoryEvent,
    digest,
)

__all__ = [
    "EVENT_TYPES",
    "HARNESSES",
    "OUTCOMES",
    "TRAJECTORY_SCHEMA",
    "TrajectoryEvent",
    "TrajectoryWriter",
    "default_root",
    "digest",
    "events_to_turn_records",
    "load_v2_turns",
    "register_all",
    "verify_file",
]
