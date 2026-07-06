"""Wave 2 reporting.chart_rules: 图族选择 + 充分性 + fallback。"""

from __future__ import annotations

from data_analysis_agent.reporting.chart_rules import (
    MIN_SCATTER_POINTS,
    MIN_TREND_POINTS,
    check_data_sufficiency,
    select_family,
    suggest_fallback,
)
from data_analysis_agent.reporting.contract import ChartFamily

# ---- select_family ----


def test_single_value_to_kpi_card():
    assert (
        select_family(
            n_points=None,
            n_categories=None,
            is_time_series=False,
            comparison_basis=None,
            single_value=True,
        )
        is ChartFamily.KPI_CARD
    )


def test_ordered_stages_to_funnel():
    assert (
        select_family(
            n_points=None,
            n_categories=None,
            is_time_series=False,
            comparison_basis=None,
            ordered_stages=True,
        )
        is ChartFamily.FUNNEL
    )


def test_time_series_enough_points_to_line():
    assert (
        select_family(
            n_points=MIN_TREND_POINTS, n_categories=None, is_time_series=True, comparison_basis=None
        )
        is ChartFamily.LINE
    )


def test_time_series_too_few_points_to_grouped_bar():
    assert (
        select_family(
            n_points=MIN_TREND_POINTS - 1,
            n_categories=None,
            is_time_series=True,
            comparison_basis=None,
        )
        is ChartFamily.GROUPED_BAR
    )


def test_comparison_basis_to_grouped_bar():
    assert (
        select_family(
            n_points=None, n_categories=3, is_time_series=False, comparison_basis="previous_period"
        )
        is ChartFamily.GROUPED_BAR
    )


def test_ranked_categories_to_bar():
    assert (
        select_family(n_points=None, n_categories=5, is_time_series=False, comparison_basis=None)
        is ChartFamily.BAR
    )


def test_default_to_table():
    assert (
        select_family(n_points=None, n_categories=None, is_time_series=False, comparison_basis=None)
        is ChartFamily.TABLE
    )


# ---- check_data_sufficiency ----


def test_line_too_few_points():
    ok, reason = check_data_sufficiency(ChartFamily.LINE, n_points=MIN_TREND_POINTS - 1)
    assert ok is False
    assert reason == "trend_needs_more_points"


def test_line_enough_points():
    ok, reason = check_data_sufficiency(ChartFamily.LINE, n_points=MIN_TREND_POINTS)
    assert ok is True
    assert reason is None


def test_scatter_too_few_observations():
    ok, reason = check_data_sufficiency("scatter", n_observations=MIN_SCATTER_POINTS - 1)
    assert ok is False
    assert reason == "scatter_needs_more_observations"


def test_scatter_enough_observations():
    ok, _ = check_data_sufficiency("scatter", n_observations=MIN_SCATTER_POINTS)
    assert ok is True


def test_bar_always_sufficient():
    ok, _ = check_data_sufficiency(ChartFamily.BAR)
    assert ok is True


# ---- suggest_fallback ----


def test_fallback_line_to_grouped_bar():
    assert suggest_fallback(ChartFamily.LINE, n_points=2) is ChartFamily.GROUPED_BAR


def test_fallback_line_single_point_to_kpi_card():
    assert suggest_fallback(ChartFamily.LINE, n_points=1) is ChartFamily.KPI_CARD


def test_fallback_scatter_to_table():
    assert suggest_fallback(ChartFamily.SCATTER, n_observations=5) is ChartFamily.TABLE


def test_fallback_none_when_sufficient():
    assert suggest_fallback(ChartFamily.LINE, n_points=10) is None
    assert suggest_fallback(ChartFamily.BAR) is None
