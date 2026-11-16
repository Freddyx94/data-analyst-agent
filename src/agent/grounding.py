"""Checks that every number in an answer comes from a query result of the same run.

Why: a language model can write a number that no query produced. Sometimes it
is a calculation done "in its head" (the test set had "about 2.4% higher",
computed from two totals), sometimes a guess. The agent's prompt forbids this,
but a rule in a prompt is not a guarantee, so we check in code.

How: take every number in the answer and look for it in the tool results of
the run (and in the question, which may name a year or an item). A number also
counts as found when it is an honest rounding of a number there: 251336.06
written as 251,336, or 1,055,433 written as "1.06 million". Rounding is only
accepted if it changes the value by less than 0.5%, the same limit the
evaluation metric uses.

What it cannot do: it sees numbers, not meaning. A number that appears in the
tool results by coincidence counts as found, even if the answer uses it for the
wrong thing. So it catches invented and calculated numbers, not every error.
"""

from __future__ import annotations

import math
import re

ROUNDING_TOLERANCE = 0.005  # the same 0.5% the evaluation metric allows
SMALL_WHOLE_NUMBER = 12     # 1 to 12 are allowed anywhere: months, "top 5", "3 departments"

# A number not glued to letters or underscores, so FOODS_3_090 or d_17 are not
# read as numbers. Commas group thousands ("1,055,433").
_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\w)"
    r"(\s*(?:million|thousand|billion)\b)?",
    flags=re.IGNORECASE,
)
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9}

# The day in a date ("31 Dec", "May 10th") is calendar knowledge, not a result,
# so it is removed before checking. The year next to it is still checked.
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# A day is a whole number of one or two digits, never part of a longer number like 87,988.
_DAY = r"(?<![\d,.])\d{1,2}(?:st|nd|rd|th)?(?!\d|[,.]\d)"
_DAY_BEFORE_MONTH = re.compile(rf"\b{_DAY}(?=\s*(?:-|to|–)?\s*\d{{0,2}}\s*{_MONTH}\b)", re.IGNORECASE)
_DAY_AFTER_MONTH = re.compile(rf"(\b{_MONTH})\s+{_DAY}", re.IGNORECASE)


def _without_days_of_dates(text: str) -> str:
    text = _DAY_BEFORE_MONTH.sub(" ", text)
    return _DAY_AFTER_MONTH.sub(r"\1 ", text)  # keep the month, drop the day


def extract_numbers(text: str) -> list[tuple[str, float]]:
    """Return (as written, value) for every number in the text. Signs are ignored."""
    found = []
    for match in _NUMBER.finditer(text):
        digits, scale = match.group(1), (match.group(2) or "").strip().lower()
        value = float(digits.replace(",", "")) * _SCALE.get(scale, 1)
        found.append((match.group(0).strip(), value))
    return found


def is_rounding_of(exact: float, shown: float) -> bool:
    """True if `shown` is `exact` rounded to some number of digits, and close to it."""
    if math.isclose(exact, shown, rel_tol=1e-9, abs_tol=1e-9):
        return True
    if abs(shown - exact) > ROUNDING_TOLERANCE * abs(exact):
        return False
    # Digits after the point (2 -> 251336.06) and before it (-4 -> 1060000).
    return any(math.isclose(round(exact, d), shown, rel_tol=1e-9, abs_tol=1e-9) for d in range(-9, 7))


def ungrounded_numbers(answer: str, sources: list[str]) -> list[str]:
    """Numbers in the answer that are not found in any source text.

    The sources are the question, the agent's own SQL (a number it filtered on,
    like a year, may be named) and the tool results.
    """
    known = {value for text in sources for _, value in extract_numbers(text)}
    missing = []
    for written, value in extract_numbers(_without_days_of_dates(answer)):
        if value == int(value) and value <= SMALL_WHOLE_NUMBER:
            continue
        if value in known or any(is_rounding_of(exact, value) for exact in known):
            continue
        missing.append(written)
    return missing


def sources_from_trace(events: list[dict]) -> list[str]:
    """The texts a run's answer may take numbers from: the question, the agent's SQL, tool results."""
    sources = [events[0].get("question", "")]
    for event in events:
        if event.get("event") == "tool_result":
            if event.get("name") == "run_sql":
                sources.append(str(event.get("arguments", {}).get("sql", "")))
            sources.append(str(event.get("result", "")))
    return sources
