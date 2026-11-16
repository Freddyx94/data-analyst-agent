"""Scores saved results again against the current gold queries, without calling a model.

    python -m evals.rescore              the development results
    python -m evals.rescore --split test

Why: when a gold query changes (for example a new accepted meaning), the runs we
already paid for should be judged by the same rules as new runs. Each results
CSV stores the final query and the answer of every run, so the final query is
run again on the database and compared with the gold. No model call, no cost.

Only `correct` and `failure_type` change. Everything else stays as measured.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import asdict

from agent.config import load_config, project_path
from agent.db import connect
from evals.questions import load_questions
from evals.run import fetch_rows, save_rows, score
from evals.setups import Outcome
from evals.summary import read_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Score saved results against the current gold.")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    args = parser.parse_args()

    questions = {q.id: q for q in load_questions(args.split)}
    con = connect()
    gold = {qid: [fetch_rows(con, sql) for sql in q.gold_sql] for qid, q in questions.items()}
    results_dir = project_path(load_config()["paths"]["results_dir"])

    for path in sorted(results_dir.glob(f"eval_*_{args.split}.csv")):
        rows = read_rows(path)
        changed: Counter[str] = Counter()
        for row in rows:
            outcome = Outcome(
                answer=row.answer,
                final_sql=row.final_sql or None,
                # The CSV does not store why the run stopped; a step-limit stop is
                # recorded as its failure type, so it is recovered from there.
                stopped="max_steps" if row.failure_type == "max_steps" else "answer",
                steps=row.steps, input_tokens=row.input_tokens, output_tokens=row.output_tokens,
                cost_usd=row.cost_usd, spent_usd=0.0, seconds=row.seconds,
            )
            correct, failure_type = score(questions[row.question_id], outcome, gold[row.question_id], con)
            if (correct, failure_type) != (row.correct, row.failure_type):
                changed[f"{row.question_id} run {row.run}: {row.failure_type or 'correct'} -> {failure_type or 'correct'}"] += 1
                row.correct, row.failure_type = correct, failure_type
        if changed:
            save_rows(rows, path)
        print(f"{path.name}: {len(changed)} runs changed")
        for change in changed:
            print(f"   {change}")
    con.close()


if __name__ == "__main__":
    main()
