"""Loads the evaluation questions from evals/questions_dev.yaml and questions_test.yaml.

File format, one entry per question:
    - id: dev-01
      level: lookup          # lookup, aggregation, join, time_window, multi_step,
                             # ambiguous, unanswerable or data_lookup
      question: "..."
      gold_sql: SELECT ...   # one query, or a list of queries for ambiguous questions
      ordered: true          # only when the question asks for an order
      notes: "..."           # why the gold is what it is (for people, not used by code)
Unanswerable questions have no gold_sql: the correct behaviour is to decline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

EVALS_DIR = Path(__file__).parent
LEVELS = {
    "lookup", "aggregation", "join", "time_window", "multi_step", "ambiguous", "unanswerable",
    "data_lookup",  # the value to filter on cannot be guessed from the schema
}


@dataclass
class Question:
    id: str
    level: str
    question: str
    gold_sql: list[str] = field(default_factory=list)  # empty for unanswerable questions
    ordered: bool = False
    notes: str = ""

    @property
    def answerable(self) -> bool:
        return self.level != "unanswerable"


def load_questions(split: str) -> list[Question]:
    """Load the "dev" or "test" questions and check that the file is well-formed."""
    if split not in {"dev", "test"}:
        raise ValueError(f"split must be 'dev' or 'test', not {split!r}")
    entries = yaml.safe_load((EVALS_DIR / f"questions_{split}.yaml").read_text(encoding="utf-8"))

    questions = []
    for entry in entries:
        gold = entry.get("gold_sql", [])
        gold = [gold] if isinstance(gold, str) else list(gold)
        question = Question(
            id=entry["id"],
            level=entry["level"],
            question=entry["question"].strip(),
            gold_sql=[sql.strip() for sql in gold],
            ordered=bool(entry.get("ordered", False)),
            notes=str(entry.get("notes", "")).strip(),
        )
        if question.level not in LEVELS:
            raise ValueError(f"{question.id}: unknown level {question.level!r}")
        if question.answerable and not question.gold_sql:
            raise ValueError(f"{question.id}: an answerable question needs gold_sql")
        if not question.answerable and question.gold_sql:
            raise ValueError(f"{question.id}: an unanswerable question must not have gold_sql")
        questions.append(question)

    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate question ids in the {split} file")
    return questions
