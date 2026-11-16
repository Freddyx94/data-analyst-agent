"""Small rules about answers, shared by the agent loop and the evaluation.

Why one place: the loop must recognise a decline (to allow a submission
without a query) and the evaluation must recognise it (to score unanswerable
questions). If the two used different rules, the agent could pass one and
fail the other.
"""

from __future__ import annotations

import re


def is_decline(answer: str) -> bool:
    """Did the agent say it cannot answer? The system prompt asks for this exact phrase."""
    text = answer.lower().replace("’", "'")  # treat a curly apostrophe like a straight one
    return "can't answer" in text or "cannot answer" in text


def row_count(run_sql_result: str) -> int | None:
    """Read the number of rows from the text run_sql returns.

    run_sql ends its text with "(5 rows)", or with "(showing the first 200 rows;
    the result has more ...)" when the result was cut. Returns None for errors.
    """
    last_line = run_sql_result.rstrip().splitlines()[-1] if run_sql_result.strip() else ""
    complete = re.fullmatch(r"\((\d+) rows\)", last_line)
    if complete:
        return int(complete.group(1))
    cut = re.match(r"\(showing the first (\d+) rows", last_line)
    if cut:
        return int(cut.group(1)) + 1  # more than were shown
    return None
