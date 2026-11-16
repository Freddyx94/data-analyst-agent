"""Runs one setup on the evaluation questions and saves the results.

    python -m evals.run --setup baseline
    python -m evals.run --setup agent
    python -m evals.run --setup agent --model claude-sonnet-5-5

Each question is asked `runs` times (config.yaml: evaluation.runs_per_setup),
because a model does not give the same answer every time. One row per question
and run goes to results/eval_<setup>_<model>_<split>.csv.

Model replies are cached, so running the same command again costs nothing.
The run stops when the money really spent reaches evaluation.max_cost_per_run_usd.

The test questions are protected: --split test only works together with --final.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb

from agent.config import load_config, project_path
from agent.db import connect
from agent.llm import CachedModel, Model, ModelError, make_model
from agent.tools import SqlError, query_rows
from agent.trace import Tracer
from evals.metrics import is_correct, is_decline, results_match
from evals.questions import Question, load_questions
from evals.setups import BASELINE_PROMPT, Outcome, run_agent_setup, run_baseline

EVAL_MAX_ROWS = 10_000  # row limit when we fetch a result to score it


@dataclass
class ResultRow:
    """One line of the results CSV: one question in one run."""

    setup: str
    model: str
    prompt: str
    split: str
    run: int
    question_id: str
    level: str
    correct: bool
    failure_type: str
    steps: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    seconds: float
    final_sql: str
    answer: str


def fetch_rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    """Run a query through the agent's guardrails and return its rows."""
    _columns, rows, _cut = query_rows(con, sql, max_rows=EVAL_MAX_ROWS, timeout_seconds=60)
    return rows


def score(
    question: Question, outcome: Outcome, gold_results: list[list[tuple[Any, ...]]],
    con: duckdb.DuckDBPyConnection,
) -> tuple[bool, str]:
    """Return (correct?, failure type). The failure type is empty when correct.

    These types are only what code can see. Finer reasons (wrong join, wrong
    date filter, misunderstood term) need a person reading the trace.
    """
    declined = is_decline(outcome.answer)
    if not question.answerable:
        return (True, "") if declined else (False, "answered_unanswerable")
    if declined:
        return False, "gave_up"
    if outcome.stopped == "max_steps":
        return False, "max_steps"
    if not outcome.final_sql:
        return False, "no_query"

    try:
        pred_rows = fetch_rows(con, outcome.final_sql)
    except SqlError:
        return False, "sql_error"

    if is_correct(True, gold_results, outcome.answer, pred_rows, question.ordered):
        return True, ""
    # Wrong: is it the shape (number of rows) or the values?
    same_row_count = any(len(gold) == len(pred_rows) for gold in gold_results)
    if same_row_count:
        # Same rows but in another order counts as its own type.
        if question.ordered and any(results_match(gold, pred_rows, ordered=False) for gold in gold_results):
            return False, "wrong_order"
        return False, "wrong_values"
    return False, "wrong_row_count"


def evaluate(
    setup: str, model: Model, split: str, runs: int, con: duckdb.DuckDBPyConnection,
    budget_usd: float, cache_dir: Path | None, trace_dir: Path | None = None,
) -> tuple[list[ResultRow], float, bool]:
    """Run every question `runs` times. Return (rows, money spent, stopped early?)."""
    questions = load_questions(split)
    gold = {q.id: [fetch_rows(con, sql) for sql in q.gold_sql] for q in questions}
    prompt = BASELINE_PROMPT if setup == "baseline" else load_config()["agent"]["system_prompt"]

    rows: list[ResultRow] = []
    spent = 0.0
    for run in range(1, runs + 1):
        # The run number is part of the cache key: each run is its own sample.
        run_model = CachedModel(model, cache_dir, salt=f"run{run}") if cache_dir else model
        for question in questions:
            if spent >= budget_usd:
                return rows, spent, True
            if setup == "baseline":
                outcome = run_baseline(question.question, run_model, con)
            else:
                tracer = None
                if trace_dir:
                    path = trace_dir / f"run{run}_{question.id}.jsonl"
                    path.unlink(missing_ok=True)  # replace the trace of an earlier evaluation
                    tracer = Tracer(path)
                outcome = run_agent_setup(question.question, run_model, con, tracer)
            spent += outcome.spent_usd
            correct, failure_type = score(question, outcome, gold[question.id], con)
            rows.append(
                ResultRow(
                    setup=setup, model=model.name, prompt=prompt, split=split, run=run,
                    question_id=question.id, level=question.level, correct=correct,
                    failure_type=failure_type, steps=outcome.steps,
                    input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
                    cost_usd=round(outcome.cost_usd, 6), seconds=round(outcome.seconds, 2),
                    final_sql=" ".join((outcome.final_sql or "").split()),
                    answer=" ".join(outcome.answer.split()),
                )
            )
            mark = "ok  " if correct else "FAIL"
            print(f"  run {run} {question.id:8} {mark} {failure_type}", flush=True)
    return rows, spent, False


def save_rows(rows: list[ResultRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def summarize(rows: list[ResultRow]) -> dict[str, Any]:
    """Accuracy per run (mean and range), plus average cost, time and steps per question."""
    runs = sorted({row.run for row in rows})
    per_run = [
        sum(r.correct for r in rows if r.run == run) / sum(1 for r in rows if r.run == run)
        for run in runs
    ]
    n = len(rows)
    return {
        "runs": len(runs),
        "questions": n // len(runs),
        "accuracy_mean": sum(per_run) / len(per_run),
        "accuracy_min": min(per_run),
        "accuracy_max": max(per_run),
        "cost_per_question_usd": sum(r.cost_usd for r in rows) / n,
        "seconds_per_question": sum(r.seconds for r in rows) / n,
        "steps_per_question": sum(r.steps for r in rows) / n,
        "total_cost_usd": sum(r.cost_usd for r in rows),
    }


def print_summary(rows: list[ResultRow]) -> None:
    s = summarize(rows)
    print(f"\n{rows[0].setup} / {rows[0].model} / {rows[0].split}: {s['questions']} questions x {s['runs']} runs")
    print(
        f"  accuracy: mean {s['accuracy_mean']:.1%} "
        f"(range {s['accuracy_min']:.1%} to {s['accuracy_max']:.1%})"
    )
    print(
        f"  per question: ${s['cost_per_question_usd']:.5f}, "
        f"{s['seconds_per_question']:.1f} s, {s['steps_per_question']:.1f} steps"
    )
    print(f"  total cost at list price: ${s['total_cost_usd']:.4f}")

    print("  accuracy by level (all runs together):")
    for level in dict.fromkeys(r.level for r in rows):
        level_rows = [r for r in rows if r.level == level]
        right = sum(r.correct for r in level_rows)
        print(f"    {level:13} {right:3}/{len(level_rows):<3} {right / len(level_rows):.0%}")

    failures: dict[str, int] = {}
    for row in rows:
        if not row.correct:
            failures[row.failure_type] = failures.get(row.failure_type, 0) + 1
    if failures:
        print("  failure types (all runs together):")
        for name, count in sorted(failures.items(), key=lambda item: -item[1]):
            print(f"    {name:22} {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one evaluation setup.")
    parser.add_argument("--setup", choices=["baseline", "agent"], required=True)
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--model", default=None, help="model name; default is the one in config.yaml")
    parser.add_argument("--runs", type=int, default=None)
    parser.add_argument("--final", action="store_true", help="required to run the test questions")
    args = parser.parse_args()

    if args.split == "test" and not args.final:
        parser.error("The test questions are only for the final numbers. Add --final if this is that run.")

    config = load_config()
    runs = args.runs or config["evaluation"]["runs_per_setup"]
    budget = config["evaluation"]["max_cost_per_run_usd"]
    try:
        model = make_model(args.model)
    except ModelError as error:
        print(f"Cannot start: {error}")
        return

    name = f"{args.setup}_{model.name}_{args.split}"
    con = connect()
    print(f"Evaluating {name}: {runs} runs, spending limit ${budget:.2f}")
    try:
        rows, spent, stopped_early = evaluate(
            args.setup, model, args.split, runs, con, budget,
            cache_dir=project_path(config["paths"]["llm_cache_dir"]),
            trace_dir=project_path(config["paths"]["traces_dir"]) / "eval" / name,
        )
    except ModelError as error:
        # Replies so far are in the cache, so running again continues from here for free.
        print(f"\nStopped: {error}\nRun the same command again to continue.")
        return
    finally:
        con.close()

    if stopped_early:
        print(f"\nSTOPPED EARLY: the spending limit of ${budget:.2f} was reached. Nothing was saved.")
        print("Raise evaluation.max_cost_per_run_usd in config.yaml, then run again (finished replies are cached).")
        return

    path = project_path(config["paths"]["results_dir"]) / f"eval_{name}.csv"
    save_rows(rows, path)
    print_summary(rows)
    print(f"\n  money spent in this run: ${spent:.4f} (cached replies cost nothing)")
    print(f"  saved: {path}")


if __name__ == "__main__":
    main()
