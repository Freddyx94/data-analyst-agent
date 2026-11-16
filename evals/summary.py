"""Builds the comparison table from the result files in results/.

    python -m evals.summary            the dev results
    python -m evals.summary --split test

It reads every results/eval_*_<split>.csv, prints one line per setup, and
writes the same table to results/summary_<split>.csv. Every number in
FINDINGS.md and README.md must come from this output.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from agent.config import load_config, project_path
from evals.run import ResultRow, summarize


def read_rows(path: Path) -> list[ResultRow]:
    """Load one results CSV back into ResultRow objects."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for line in csv.DictReader(f):
            rows.append(
                ResultRow(
                    setup=line["setup"], model=line["model"], prompt=line["prompt"],
                    split=line["split"], run=int(line["run"]), question_id=line["question_id"],
                    level=line["level"], correct=line["correct"] == "True",
                    failure_type=line["failure_type"], steps=int(line["steps"]),
                    input_tokens=int(line["input_tokens"]), output_tokens=int(line["output_tokens"]),
                    cost_usd=float(line["cost_usd"]), seconds=float(line["seconds"]),
                    final_sql=line["final_sql"], answer=line["answer"],
                )
            )
    return rows


def build_table(split: str) -> list[dict[str, str]]:
    results_dir = project_path(load_config()["paths"]["results_dir"])
    table = []
    for path in sorted(results_dir.glob(f"eval_*_{split}.csv")):
        rows = read_rows(path)
        s = summarize(rows)
        table.append(
            {
                "setup": rows[0].setup,
                "model": rows[0].model,
                "prompt": rows[0].prompt,
                "questions": str(s["questions"]),
                "runs": str(s["runs"]),
                "accuracy_mean": f"{s['accuracy_mean']:.1%}",
                "accuracy_range": f"{s['accuracy_min']:.1%} to {s['accuracy_max']:.1%}",
                "cost_per_question_usd": f"{s['cost_per_question_usd']:.5f}",
                "seconds_per_question": f"{s['seconds_per_question']:.1f}",
                "steps_per_question": f"{s['steps_per_question']:.1f}",
            }
        )
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare the evaluation results.")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    args = parser.parse_args()

    table = build_table(args.split)
    if not table:
        print(f"No results for the {args.split} questions yet. Run python -m evals.run first.")
        return

    columns = list(table[0].keys())
    widths = {c: max(len(c), *(len(row[c]) for row in table)) for c in columns}
    print(" | ".join(c.ljust(widths[c]) for c in columns))
    for row in table:
        print(" | ".join(row[c].ljust(widths[c]) for c in columns))

    path = project_path(load_config()["paths"]["results_dir"]) / f"summary_{args.split}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(table)
    print(f"\nsaved: {path}")


if __name__ == "__main__":
    main()
