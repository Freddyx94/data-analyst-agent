"""Decides which chart, if any, fits a query result.

Why in code and not by the model: the choice follows a few clear rules, so code
makes it the same way every time and costs nothing. The app draws the chart;
this module only decides what to draw, so it can be tested without the app.

The rules:
- At least 2 rows, one label column (x) and one number column (y).
- Dates, or a column named like year, month, week or date: a line chart.
- Anything else (departments, items, weekdays): a bar chart, at most 50 bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

MAX_BARS = 50
TIME_WORDS = ("date", "day", "week", "month", "year", "quarter")


@dataclass
class ChartSpec:
    kind: str  # "bar" or "line"
    x: str     # column name for the horizontal axis
    y: str     # column name for the values


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _is_time_column(name: str, values: list[Any]) -> bool:
    if all(isinstance(v, date) for v in values):
        return True
    return any(word in name.lower() for word in TIME_WORDS)


def choose_chart(columns: list[str], rows: list[tuple[Any, ...]]) -> ChartSpec | None:
    """Return the chart to draw for this result, or None when a chart would not help."""
    if len(rows) < 2 or len(columns) < 2:
        return None
    values = {name: [row[i] for row in rows] for i, name in enumerate(columns)}
    numeric = [c for c in columns if all(_is_number(v) for v in values[c] if v is not None)]

    # x: the first column that is not a plain number, or a number column named like
    # a time unit (year 2014, month 5). y: the first other number column.
    x = next((c for c in columns if c not in numeric), None)
    if x is None:
        x = next((c for c in numeric if _is_time_column(c, values[c])), None)
    if x is None:
        return None
    y = next((c for c in numeric if c != x), None)
    if y is None:
        return None

    if _is_time_column(x, values[x]):
        return ChartSpec("line", x, y)
    if len(rows) > MAX_BARS:
        return None
    return ChartSpec("bar", x, y)
