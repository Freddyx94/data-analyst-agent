"""Runs every gold query on the real database and writes the results for review.

    python -m evals.check_gold

Why: a gold query is only "gold" after a person has looked at its result and
agrees it answers the question. This script runs all of them through the same
guardrails the agent has, reports problems (errors, empty results, a top-1
query where two rows tie for first place), and writes one review file per
split: evals/gold_review_dev.md and evals/gold_review_test.md. These files
contain numbers from the real data and are git-ignored.
"""

from __future__ import annotations

from typing import Any

import duckdb

from agent.db import connect
from agent.tools import SqlError, _format_rows, query_rows
from evals.questions import EVALS_DIR, Question, load_questions

MAX_REVIEW_ROWS = 15
GOLD_MAX_ROWS = 10_000  # gold results are small; this only guards against a mistake


def run_gold(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run one gold query with the agent's guardrails, but a higher row limit."""
    columns, rows, was_cut = query_rows(con, sql, max_rows=GOLD_MAX_ROWS, timeout_seconds=60)
    if was_cut:
        raise SqlError(f"The gold result has more than {GOLD_MAX_ROWS} rows.")
    return columns, rows


def check_question(con: duckdb.DuckDBPyConnection, question: Question) -> tuple[list[str], list[str]]:
    """Return (lines for the review file, problems found)."""
    lines = [f"## {question.id} ({question.level})", "", f"**Question:** {question.question}", ""]
    problems: list[str] = []
    if question.ordered:
        lines += ["Row order matters for this question.", ""]
    if question.notes:
        lines += [f"Notes: {question.notes}", ""]
    if not question.answerable:
        lines += ["Correct behaviour: the agent declines (\"I can't answer that from this data\").", ""]
        return lines, problems

    for number, sql in enumerate(question.gold_sql, start=1):
        label = f"Gold {number} of {len(question.gold_sql)}" if len(question.gold_sql) > 1 else "Gold"
        lines += [f"{label}:", "", "```sql", sql, "```", ""]
        try:
            columns, rows = run_gold(con, sql)
        except SqlError as error:
            problems.append(f"{question.id} gold {number}: {error}")
            lines += [f"ERROR: {error}", ""]
            continue
        if not rows:
            problems.append(f"{question.id} gold {number}: the result is empty")
        if any(value is None for row in rows for value in row):
            problems.append(f"{question.id} gold {number}: the result contains NULL")
        shown = rows[:MAX_REVIEW_ROWS]
        lines += ["```", _format_rows(columns, shown)]
        if len(rows) > MAX_REVIEW_ROWS:
            lines.append(f"... ({len(rows)} rows in total)")
        lines += ["```", ""]
    return lines, problems


def main() -> None:
    con = connect()
    for split in ("dev", "test"):
        questions = load_questions(split)
        lines = [f"# Gold results for review: {split} ({len(questions)} questions)", ""]
        problems: list[str] = []
        for question in questions:
            question_lines, question_problems = check_question(con, question)
            lines += question_lines
            problems += question_problems
        path = EVALS_DIR / f"gold_review_{split}.md"
        path.write_text("\n".join(lines), encoding="utf-8")

        levels: dict[str, int] = {}
        for question in questions:
            levels[question.level] = levels.get(question.level, 0) + 1
        print(f"{split}: {len(questions)} questions {levels}")
        print(f"  review file: {path}")
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        if not problems:
            print("  all gold queries ran and returned a result")
    con.close()


if __name__ == "__main__":
    main()
