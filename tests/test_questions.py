"""Tests for the question files. They need no real data and no model."""

from __future__ import annotations

from collections import Counter

import pytest

from agent.tools import query_rows
from evals.questions import load_questions

EXPECTED_MIX = {
    "dev": {"lookup": 4, "aggregation": 5, "join": 5, "time_window": 5, "multi_step": 4, "ambiguous": 3, "unanswerable": 5, "data_lookup": 5},
    "test": {"lookup": 3, "aggregation": 4, "join": 4, "time_window": 3, "multi_step": 3, "ambiguous": 1, "unanswerable": 3, "data_lookup": 3},
}


@pytest.mark.parametrize("split, total", [("dev", 36), ("test", 24)])
def test_question_files_have_the_planned_size_and_mix(split, total):
    questions = load_questions(split)
    assert len(questions) == total
    assert dict(Counter(q.level for q in questions)) == EXPECTED_MIX[split]


def test_no_question_is_in_both_splits():
    dev = {q.question.lower() for q in load_questions("dev")}
    test = {q.question.lower() for q in load_questions("test")}
    assert not dev & test


def test_only_ambiguous_questions_have_several_meanings():
    for question in load_questions("dev") + load_questions("test"):
        if question.level == "unanswerable":
            assert question.gold_sql == []
        elif question.level != "ambiguous" and len(question.gold_sql) > 1:
            # Allowed only when the notes explain it (same meaning, different form).
            assert "only because" in question.notes, question.id


@pytest.mark.parametrize("split", ["dev", "test"])
def test_every_gold_query_passes_the_guardrails_and_runs(con, split):
    """Runs on the fixture database: the values are invented, but a typo or a
    table outside the allowed list fails here, without the real data."""
    for question in load_questions(split):
        for sql in question.gold_sql:
            columns, _rows, _cut = query_rows(con, sql)
            assert columns, question.id
