"""Hand-made examples for the chart choice."""

from __future__ import annotations

from datetime import date

from agent.chart import ChartSpec, choose_chart


def test_categories_get_a_bar_chart():
    rows = [("FOODS_1", 124784), ("FOODS_2", 179996), ("FOODS_3", 750653)]
    assert choose_chart(["dept_id", "units"], rows) == ChartSpec("bar", "dept_id", "units")


def test_dates_get_a_line_chart():
    rows = [(date(2016, 5, 20), 3100), (date(2016, 5, 21), 3500), (date(2016, 5, 22), 3300)]
    assert choose_chart(["date", "units"], rows) == ChartSpec("line", "date", "units")


def test_a_year_or_month_number_is_used_as_the_time_axis():
    rows = [(2014, 1101681), (2015, 1055433), (2016, 424370)]
    assert choose_chart(["year", "units"], rows) == ChartSpec("line", "year", "units")
    months = [(1, 87988), (2, 79166), (3, 90280)]
    assert choose_chart(["month", "units"], months) == ChartSpec("line", "month", "units")


def test_the_first_number_column_is_plotted():
    rows = [("FOODS_1", 120451, 124784, 3.6), ("FOODS_2", 165679, 179996, 8.64)]
    assert choose_chart(["dept_id", "u2014", "u2015", "pct"], rows) == ChartSpec("bar", "dept_id", "u2014")


def test_no_chart_for_one_row_or_one_column():
    assert choose_chart(["units"], [(1055433,)]) is None
    assert choose_chart(["dept_id", "units"], [("FOODS_3", 750653)]) is None
    assert choose_chart(["item_id"], [("a",), ("b",)]) is None


def test_no_chart_without_numbers_or_with_too_many_bars():
    assert choose_chart(["event_name", "event_type"], [("SuperBowl", "Sporting"), ("Easter", "Cultural")]) is None
    many = [(f"FOODS_3_{i:03d}", i) for i in range(60)]
    assert choose_chart(["item_id", "units"], many) is None


def test_booleans_are_labels_not_numbers():
    rows = [(True, 3093.5), (False, 2804.1)]  # SNAP day yes/no: two bars
    assert choose_chart(["is_snap", "avg_units"], rows) == ChartSpec("bar", "is_snap", "avg_units")
    flags = [("FOODS_1", True), ("FOODS_2", False)]  # a flag is never the value that is plotted
    assert choose_chart(["dept_id", "is_new"], flags) is None


def test_empty_values_do_not_break_the_choice():
    rows = [("FOODS_1", None), ("FOODS_2", 5)]
    assert choose_chart(["dept_id", "units"], rows) == ChartSpec("bar", "dept_id", "units")
