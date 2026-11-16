"""Decides whether the agent's result is the same answer as the gold result.

Why this is its own file with its own tests: every accuracy number of the
project comes out of these few functions. If they are too strict, correct
answers count as wrong; if they are too loose, wrong answers count as correct.
The rules are written down here and each has a hand-made test example.

The rules:
- Column names do not matter. The agent may return extra columns; every gold
  column must be found among the agent's columns.
- The number of rows must be the same.
- Row order matters only if the question asks for an order (ordered=True).
- Whole numbers must be exactly equal.
- Decimal numbers may be rounded, if rounding changes the value by less than 0.5%.
- Text is compared without upper/lower case and outer spaces.
"""

from __future__ import annotations

import itertools
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

Row = tuple[Any, ...]

ROUNDING_TOLERANCE = 0.005  # a rounded number may differ from the exact one by 0.5%
MAX_COLUMN_COMBINATIONS = 2000  # safety limit for very wide results


def normalize(value: Any) -> Any:
    """Bring a database value into a simple form: None, bool, int, float or text."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float):
        return value
    if isinstance(value, datetime):
        # A timestamp at midnight is the same answer as the plain date.
        is_midnight = (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0)
        return value.date().isoformat() if is_midnight else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip().lower()


def values_match(gold: Any, pred: Any) -> bool:
    """Is the agent's value (pred) the same answer as the gold value?"""
    gold, pred = normalize(gold), normalize(pred)
    if gold is None or pred is None:
        return gold is None and pred is None
    if isinstance(gold, bool) or isinstance(pred, bool):
        return isinstance(gold, bool) and isinstance(pred, bool) and gold == pred
    if isinstance(gold, str) or isinstance(pred, str):
        return isinstance(gold, str) and isinstance(pred, str) and gold == pred

    # Both are numbers from here on.
    if math.isnan(gold) or math.isnan(pred):
        return math.isnan(gold) and math.isnan(pred)
    if isinstance(gold, int):
        return abs(pred - gold) < 1e-9  # a count must be exact (398.0 equals 398)
    if math.isclose(gold, pred, rel_tol=1e-6, abs_tol=1e-9):
        return True
    return _is_acceptable_rounding(gold, pred)


def _is_acceptable_rounding(gold: float, pred: float) -> bool:
    """True if pred is gold rounded to some number of decimals, and close enough.

    Example: gold 2.6130 and pred 2.61 match (rounded to 2 decimals, 0.1% off).
    Gold 0.7543 and pred 0.7565 do not match: 0.7565 is not a rounding of 0.7543.
    """
    if abs(pred - gold) > ROUNDING_TOLERANCE * abs(gold):
        return False
    return any(math.isclose(round(gold, d), pred, rel_tol=1e-9, abs_tol=1e-12) for d in range(0, 7))


def results_match(gold_rows: list[Row], pred_rows: list[Row], ordered: bool = False) -> bool:
    """Does the agent's result table contain the gold result?

    We look for a way to pick one agent column for every gold column so that
    the picked columns, read row by row, equal the gold rows.
    """
    if len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        return True
    gold_width, pred_width = len(gold_rows[0]), len(pred_rows[0])
    if pred_width < gold_width:
        return False

    gold = [tuple(normalize(v) for v in row) for row in gold_rows]
    pred = [tuple(normalize(v) for v in row) for row in pred_rows]

    # Step 1: for each gold column, which agent columns hold the same values?
    candidates = []
    for g in range(gold_width):
        gold_column = [row[g] for row in gold]
        matching = [
            p for p in range(pred_width)
            if _columns_match(gold_column, [row[p] for row in pred], ordered)
        ]
        if not matching:
            return False
        candidates.append(matching)

    # Step 2: values must also line up row by row (the right number next to
    # the right label), so check whole rows for each possible choice of columns.
    combinations = itertools.islice(itertools.product(*candidates), MAX_COLUMN_COMBINATIONS)
    for choice in combinations:
        if len(set(choice)) < len(choice):
            continue  # one agent column cannot stand for two gold columns
        projected = [tuple(row[p] for p in choice) for row in pred]
        if _rows_match(gold, projected, ordered):
            return True
    return False


def _columns_match(gold_column: list[Any], pred_column: list[Any], ordered: bool) -> bool:
    if not ordered:
        gold_column = sorted(gold_column, key=_sort_key)
        pred_column = sorted(pred_column, key=_sort_key)
    return all(values_match(g, p) for g, p in zip(gold_column, pred_column))


def _rows_match(gold: list[Row], pred: list[Row], ordered: bool) -> bool:
    if ordered:
        return all(_row_equal(g, p) for g, p in zip(gold, pred))
    # Without an order, every gold row must find its own partner among the agent's rows.
    remaining = list(pred)
    for gold_row in gold:
        for i, pred_row in enumerate(remaining):
            if _row_equal(gold_row, pred_row):
                del remaining[i]
                break
        else:
            return False
    return True


def _row_equal(gold_row: Row, pred_row: Row) -> bool:
    return all(values_match(g, p) for g, p in zip(gold_row, pred_row))


def _sort_key(value: Any) -> tuple[int, Any]:
    """Sort values of mixed types without errors: empty first, then numbers, then text."""
    if value is None:
        return (0, 0)
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, value)
    return (3, value)


def is_decline(answer: str) -> bool:
    """Did the agent say it cannot answer? The system prompt asks for this exact phrase."""
    text = answer.lower().replace("’", "'")  # treat a curly apostrophe like a straight one
    return "can't answer" in text or "cannot answer" in text


def is_correct(
    answerable: bool,
    gold_results: list[list[Row]],
    answer: str,
    pred_rows: list[Row] | None,
    ordered: bool = False,
) -> bool:
    """The final verdict for one question.

    answerable=False: correct only if the agent declines.
    answerable=True: the agent must not decline, and its final result must match
    one of the gold results (ambiguous questions have more than one).
    """
    if not answerable:
        return is_decline(answer)
    if is_decline(answer) or pred_rows is None:
        return False
    return any(results_match(gold, pred_rows, ordered) for gold in gold_results)
