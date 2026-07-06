"""报告领域层(Wave 2):图表规则。

确定性的图族选择、数据充分性检查、fallback 建议。无 LLM、无 I/O。

- ``select_family``:按数据形态(单值/有序阶段/时序/对比/分类排名)推荐图族。
- ``check_data_sufficiency``:趋势图点数过少、散点观测过少 → 不充分。
- ``suggest_fallback``:不充分时给替代图族(线→分组柱/KPI 卡;散点→表)。

与 spec §4.7「Chart Spec」的图族语义同源;封闭词表 ``ChartFamily`` 定义于 contract.py。
"""

from __future__ import annotations

from data_analysis_agent.reporting.contract import ChartFamily

__all__ = [
    "MIN_TREND_POINTS",
    "MIN_SCATTER_POINTS",
    "select_family",
    "check_data_sufficiency",
    "suggest_fallback",
]

MIN_TREND_POINTS = 3
MIN_SCATTER_POINTS = 10
_RANK_BAR_MAX_CATEGORIES = 12


def _coerce_family(family: ChartFamily | str) -> ChartFamily:
    if isinstance(family, ChartFamily):
        return family
    return ChartFamily(family)


def select_family(
    *,
    n_points: int | None,
    n_categories: int | None,
    is_time_series: bool,
    comparison_basis: str | None,
    single_value: bool = False,
    ordered_stages: bool = False,
) -> ChartFamily:
    """按数据形态推荐图族(确定性)。"""
    if single_value:
        return ChartFamily.KPI_CARD
    if ordered_stages:
        return ChartFamily.FUNNEL
    if is_time_series:
        if n_points is not None and n_points >= MIN_TREND_POINTS:
            return ChartFamily.LINE
        return ChartFamily.GROUPED_BAR  # 点数不足,退化为分组柱
    if comparison_basis:
        return ChartFamily.GROUPED_BAR
    if n_categories is not None and 2 <= n_categories <= _RANK_BAR_MAX_CATEGORIES:
        return ChartFamily.BAR
    return ChartFamily.TABLE


def check_data_sufficiency(
    family: ChartFamily | str,
    *,
    n_points: int | None = None,
    n_observations: int | None = None,
) -> tuple[bool, str | None]:
    """``(sufficient, reason)``;趋势点数/散点观测数不足时给出原因。"""
    f = _coerce_family(family)
    if f is ChartFamily.LINE and (n_points is None or n_points < MIN_TREND_POINTS):
        return False, "trend_needs_more_points"
    if f is ChartFamily.SCATTER and (n_observations is None or n_observations < MIN_SCATTER_POINTS):
        return False, "scatter_needs_more_observations"
    return True, None


def suggest_fallback(
    family: ChartFamily | str,
    *,
    n_points: int | None = None,
    n_observations: int | None = None,
) -> ChartFamily | None:
    """不充分时给替代图族;充分则 None。"""
    f = _coerce_family(family)
    sufficient, _ = check_data_sufficiency(f, n_points=n_points, n_observations=n_observations)
    if sufficient:
        return None
    if f is ChartFamily.LINE:
        if n_points is not None and n_points <= 1:
            return ChartFamily.KPI_CARD
        return ChartFamily.GROUPED_BAR
    if f is ChartFamily.SCATTER:
        return ChartFamily.TABLE
    return None
