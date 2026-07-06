"""报告领域层(Wave 1):上游理解层。

UserNeed / ExplicitRequirements / ImplicitRequirements / Uncertainty /
DataContext / TableInfo / ColumnInfo / ProcessContext / ProcessStep / TraceLink /
SourceKind / SourcedValue。纯 stdlib,见 ADR 0009。

设计要点:
- 所有领域数据类 ``@dataclasses.dataclass(frozen=True)`` + ``tuple`` 容器,与项目
  「state 不可变」不变量(``state_machine.py``)同源,可哈希、可比较。
- ``Serializable`` mixin 提供通用 ``to_dict`` / ``from_dict``:Enum 字段经 ``.value``
  序列化、``tuple`` 往返、嵌套 frozen dataclass 递归重建。往返契约:
  ``Cls.from_dict(x.to_dict()) == x``。
- 时间类字段(如 ``generated_at``)由调用方注入,本模块不调 ``datetime.now()``,
  保证确定性测试。
"""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from collections.abc import Mapping
from typing import Any, Union

__all__ = [
    "SourceKind",
    "SourcedValue",
    "ExplicitRequirements",
    "ImplicitRequirements",
    "Uncertainty",
    "UserNeed",
    "ColumnInfo",
    "TableInfo",
    "DataContext",
    "ProcessStep",
    "ProcessContext",
    "TraceLink",
    "Serializable",
]


class SourceKind(str, enum.Enum):
    """一段报告信息的来源(anti-hallucination 原语:推断 vs 事实必须可区分)。"""

    EXPLICIT_USER = "explicit_user"
    IMPLICIT_USER = "implicit_user"
    DATA_CONTEXT = "data_context"
    PROCESS_CONTEXT = "process_context"
    MEMORY = "memory"
    TEMPLATE = "template"


# ----------------------------- 序列化基础设施 -----------------------------

_T = typing.TypeVar("_T")
_HINTS_CACHE: dict[type, dict[str, Any]] = {}


def _resolve_hints(cls: type) -> dict[str, Any]:
    hints = _HINTS_CACHE.get(cls)
    if hints is None:
        hints = typing.get_type_hints(cls)
        _HINTS_CACHE[cls] = hints
    return hints


def _to_value(v: Any) -> Any:
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, tuple):
        return [_to_value(x) for x in v]
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _to_value(getattr(v, f.name)) for f in dataclasses.fields(v)}
    return v


def _is_union(t: Any) -> bool:
    origin = typing.get_origin(t)
    if origin is Union:
        return True
    union_t = getattr(types, "UnionType", None)
    return union_t is not None and origin is union_t


def _from_value(v: Any, t: Any) -> Any:
    if v is None:
        return None
    if t is Any or t is None:
        return v
    origin = typing.get_origin(t)
    if isinstance(t, type) and issubclass(t, enum.Enum):
        return t(v)
    if isinstance(t, type) and dataclasses.is_dataclass(t):
        if isinstance(v, Mapping):
            return t.from_dict(v)  # type: ignore[attr-defined]
        return v
    if origin is tuple:
        args = typing.get_args(t)
        if not isinstance(v, (list, tuple)):
            return v
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_from_value(x, args[0]) for x in v)
        # 固定长度 tuple(如 tuple[str|None, str|None]):按位置映射类型
        return tuple(
            _from_value(x, args[i] if i < len(args) else (args[0] if args else Any))
            for i, x in enumerate(v)
        )
    if _is_union(t):
        non_none = [a for a in typing.get_args(t) if a is not type(None)]
        if len(non_none) == 1:
            return _from_value(v, non_none[0])
        return v
    return v


class Serializable:
    """所有 reporting 领域数据类的通用 to_dict / from_dict mixin。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            f.name: _to_value(getattr(self, f.name))
            for f in dataclasses.fields(self)  # type: ignore[arg-type]
        }

    @classmethod
    def from_dict(cls: type[_T], data: Mapping[str, Any]) -> _T:
        hints = _resolve_hints(cls)
        kwargs: dict[str, Any] = {}
        for f in dataclasses.fields(cls):  # type: ignore[arg-type]
            if f.name in data:
                kwargs[f.name] = _from_value(data[f.name], hints.get(f.name, Any))
        return cls(**kwargs)


# ----------------------------- 领域数据类 -----------------------------


@dataclasses.dataclass(frozen=True)
class SourcedValue(Serializable):
    """一个值 + 它的来源 + 推断理由。"""

    value: str | None = None
    source: SourceKind = SourceKind.IMPLICIT_USER
    rationale: str = ""


@dataclasses.dataclass(frozen=True)
class ExplicitRequirements(Serializable):
    """用户显式陈述的需求(从请求 lexical 可判定的事实)。"""

    business_question: str | None = None
    requested_outputs: tuple[str, ...] = ()
    named_metrics: tuple[str, ...] = ()
    named_dimensions: tuple[str, ...] = ()
    time_window: str | None = None
    audience: str | None = None
    language: str | None = None
    format_constraints: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()
    must_avoid: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ImplicitRequirements(Serializable):
    """从用户措辞/场景推断的需求(必须标 source=implicit_user)。"""

    likely_report_type: str | None = None
    business_scenario: str | None = None
    narrative_style: str | None = None
    section_expectations: tuple[str, ...] = ()
    visual_expectations: tuple[str, ...] = ()
    decision_or_update_goal: str | None = None
    cadence: str | None = None


@dataclasses.dataclass(frozen=True)
class Uncertainty(Serializable):
    """报告前应澄清或显式假设的高影响不确定点。"""

    topic: str
    why: str
    needs_clarification: bool = False


@dataclasses.dataclass(frozen=True)
class UserNeed(Serializable):
    """用户报告需求的归一化表示:显式 vs 隐式分离 + 不确定点。"""

    raw_request: str
    explicit_requirements: ExplicitRequirements
    implicit_requirements: ImplicitRequirements
    uncertainties: tuple[Uncertainty, ...] = ()
    clarification_needed: bool = False


@dataclasses.dataclass(frozen=True)
class ColumnInfo(Serializable):
    name: str
    dtype: str | None = None
    candidate_role: str | None = None  # metric / dimension / date / identifier / unknown


@dataclasses.dataclass(frozen=True)
class TableInfo(Serializable):
    name: str  # 文件名或 sheet 名
    path: str | None = None
    columns: tuple[ColumnInfo, ...] = ()
    n_rows: int | None = None
    n_rows_sampled: int | None = None
    sampled: bool = False


@dataclasses.dataclass(frozen=True)
class DataContext(Serializable):
    """报告对数据可合法知晓的部分(从 data_profile 起,随分析结果增长)。"""

    tables: tuple[TableInfo, ...] = ()
    candidate_date_columns: tuple[str, ...] = ()
    available_date_range: tuple[str | None, str | None] = (None, None)
    candidate_metric_columns: tuple[str, ...] = ()
    candidate_dimensions: tuple[str, ...] = ()
    business_grain: str | None = None
    missingness_risks: tuple[str, ...] = ()
    duplicate_key_risks: tuple[str, ...] = ()
    join_candidates: tuple[str, ...] = ()
    data_gaps: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ProcessStep(Serializable):
    step_id: str
    tool: str
    summary: str
    assumptions: tuple[str, ...] = ()
    failed: bool = False
    recovery: str | None = None
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ProcessContext(Serializable):
    """分析过程轨迹(可观察信号:工具序列/假设/失败/派生/artifact),非心理读取。"""

    steps: tuple[ProcessStep, ...] = ()
    rejected_paths: tuple[str, ...] = ()
    user_corrections: tuple[str, ...] = ()
    sensitive_mode: bool = False  # True → 已降级(steps 应为空)


@dataclasses.dataclass(frozen=True)
class TraceLink(Serializable):
    """解释某目标(如契约字段)为何存在 → 指向其支撑来源。"""

    target: str
    source: SourceKind
    source_ref: str
    rationale: str = ""
