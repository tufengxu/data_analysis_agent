"""报告领域层(Wave 1):上下文采集器。

消费 ``data_profile`` 工具输出(file/directory 两种 shape)与摘要化工具事件 dict,
构建 ``DataContext`` / ``ProcessContext``。**纯 dict 输入**:不依赖 Event / 工具
dataclass,避免反向耦合运行时(drift 规则 + ADR 0009)。

- ``build_data_context``:启发式归类候选 日期/指标/维度 列,猜测业务粒度。
- ``build_process_context``:``sensitive_mode=True`` 时降级为空轨迹(spec §4.3 隐私)。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from data_analysis_agent.reporting.model import (
    ColumnInfo,
    DataContext,
    ProcessContext,
    ProcessStep,
    TableInfo,
)

__all__ = ["build_data_context", "build_process_context"]

_DATE_NAME_HINTS = (
    "date",
    "日期",
    "时间",
    "time",
    "day",
    "week",
    "month",
    "year",
    "年",
    "月",
    "日",
)
_DATE_DTYPE_HINTS = ("datetime", "timestamp", "date")
_NUMERIC_DTYPE_HINTS = ("int", "float", "double", "decimal", "number", "数值")
_DIM_DTYPE_HINTS = ("object", "str", "string", "category", "text", "bool")

# (en, zh) → 业务粒度猜测
_GRAIN_HINTS: tuple[tuple[str, str], ...] = (
    ("order", "订单"),
    ("user", "用户"),
    ("customer", "客户"),
    ("account", "账户"),
    ("sku", "商品"),
    ("product", "产品"),
)


def _classify_column(name: str, dtype: str | None) -> str:
    lname = name.lower()
    if dtype:
        ldtype = dtype.lower()
        if any(h in ldtype for h in _DATE_DTYPE_HINTS) or any(h in lname for h in _DATE_NAME_HINTS):
            return "date"
        if any(h in ldtype for h in _NUMERIC_DTYPE_HINTS):
            return "metric"
        if any(h in ldtype for h in _DIM_DTYPE_HINTS):
            return "dimension"
    if any(h in lname for h in _DATE_NAME_HINTS):
        return "date"
    return "unknown"


def _columns_from_table(table: Mapping[str, object]) -> list[ColumnInfo]:
    raw_cols = table.get("columns")
    if not isinstance(raw_cols, list):
        raw_cols = table.get("columns_preview")
    if not isinstance(raw_cols, list):
        raw_cols = []
    out: list[ColumnInfo] = []
    for col in raw_cols:
        if isinstance(col, Mapping):
            name = str(col.get("name", ""))
            dtype = col.get("dtype")
            dtype_s = str(dtype) if dtype is not None else None
            out.append(
                ColumnInfo(name=name, dtype=dtype_s, candidate_role=_classify_column(name, dtype_s))
            )
        elif isinstance(col, str):
            out.append(ColumnInfo(name=col, candidate_role=_classify_column(col, None)))
    return out


def _table_info_from(
    t: Mapping[str, object], name_keys: tuple[str, ...], fallback_path: str | None
) -> TableInfo:
    name = ""
    for k in name_keys:
        v = t.get(k)
        if v:
            name = str(v)
            break
    if not name:
        name = "table"
    raw_path = t.get("path")
    path = raw_path if isinstance(raw_path, str) else fallback_path
    n_rows = t.get("n_rows")
    n_rows_s = t.get("n_rows_sampled")
    return TableInfo(
        name=name,
        path=path,
        columns=tuple(_columns_from_table(t)),
        n_rows=int(n_rows) if isinstance(n_rows, int) and not isinstance(n_rows, bool) else None,
        n_rows_sampled=(
            int(n_rows_s) if isinstance(n_rows_s, int) and not isinstance(n_rows_s, bool) else None
        ),
        sampled=_as_bool(t.get("sampled")),
    )


def _extract_tables(profile: Mapping[str, object]) -> list[TableInfo]:
    raw_path = profile.get("path")
    fallback_path = raw_path if isinstance(raw_path, str) else None
    tables: list[TableInfo] = []
    raw_tables = profile.get("tables")
    if isinstance(raw_tables, list):
        for t in raw_tables:
            if isinstance(t, Mapping):
                tables.append(_table_info_from(t, ("sheet", "name"), fallback_path))
    raw_files = profile.get("files")
    if isinstance(raw_files, list):
        for f in raw_files:
            if isinstance(f, Mapping):
                tables.append(_table_info_from(f, ("name",), fallback_path))
    return tables


def _guess_grain(tables: list[TableInfo]) -> str | None:
    names = [c.name.lower() for tb in tables for c in tb.columns]
    joined = " ".join(names)
    for en, zh in _GRAIN_HINTS:
        if en in joined or zh in joined:
            return en
    return None


def build_data_context(profile: Mapping[str, object]) -> DataContext:
    """data_profile 输出 → DataContext(候选日期/指标/维度列 + 业务粒度猜测)。"""
    tables = _extract_tables(profile)
    date_cols: list[str] = []
    metric_cols: list[str] = []
    dim_cols: list[str] = []
    for tb in tables:
        for col in tb.columns:
            if col.candidate_role == "date":
                date_cols.append(col.name)
            elif col.candidate_role == "metric":
                metric_cols.append(col.name)
            elif col.candidate_role == "dimension":
                dim_cols.append(col.name)
    return DataContext(
        tables=tuple(tables),
        candidate_date_columns=tuple(date_cols),
        candidate_metric_columns=tuple(metric_cols),
        candidate_dimensions=tuple(dim_cols),
        business_grain=_guess_grain(tables),
    )


def _as_bool(v: object) -> bool:
    # 严格:仅接受真正的 True。避免 bool("false") → True 的字符串误真(评审 Low)。
    return v is True


def _to_str_tuple(v: object) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, (list, tuple)):
        return tuple(str(x) for x in v)
    return (str(v),)


def build_process_context(
    events: Iterable[Mapping[str, object]], *, sensitive_mode: bool = False
) -> ProcessContext:
    """摘要事件 dict 序列 → ProcessContext;sensitive_mode 隐私降级为空轨迹。"""
    if sensitive_mode:
        return ProcessContext(sensitive_mode=True)
    steps: list[ProcessStep] = []
    for idx, ev in enumerate(events):
        if not isinstance(ev, Mapping):
            continue
        step_id = ev.get("step_id")
        sid = str(step_id) if step_id is not None else f"step_{idx}"
        recovery = ev.get("recovery")
        steps.append(
            ProcessStep(
                step_id=sid,
                tool=str(ev.get("tool", "")),
                summary=str(ev.get("summary", "")),
                assumptions=_to_str_tuple(ev.get("assumptions")),
                failed=_as_bool(ev.get("failed")),
                recovery=str(recovery) if recovery is not None else None,
                evidence_ids=_to_str_tuple(ev.get("evidence_ids")),
                artifact_ids=_to_str_tuple(ev.get("artifact_ids")),
            )
        )
    return ProcessContext(steps=tuple(steps))
