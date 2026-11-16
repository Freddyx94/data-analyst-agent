"""Runs the number check over the saved agent traces of an evaluation.

    python -m evals.number_check              development traces
    python -m evals.number_check --split test

For every agent run it lists the numbers in the final answer that appear in no
query, query result or the question of that run (see src/agent/grounding.py).
Writes results/number_check_<split>.csv and prints the share of answers with at
least one such number. No model call, no cost. The baseline has no answer text,
so it is not checked.
"""

from __future__ import annotations

import argparse
import csv
import json

from agent.config import load_config, project_path
from agent.grounding import sources_from_trace, ungrounded_numbers


def main() -> None:
    parser = argparse.ArgumentParser(description="Number check over saved agent traces.")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    args = parser.parse_args()

    config = load_config()
    trace_root = project_path(config["paths"]["traces_dir"]) / "eval"
    rows = []
    for folder in sorted(trace_root.glob(f"agent-*_{args.split}")):
        setup, model, _split = folder.name.split("_")
        for path in sorted(folder.glob("run*.jsonl")):
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            run, question_id = path.stem.split("_", 1)
            missing = ungrounded_numbers(events[-1].get("answer", ""), sources_from_trace(events))
            rows.append({
                "setup": setup, "model": model, "question_id": question_id, "run": run.removeprefix("run"),
                "ungrounded_numbers": "; ".join(missing),
            })
    if not rows:
        print(f"No agent traces for the {args.split} questions.")
        return

    path = project_path(config["paths"]["results_dir"]) / f"number_check_{args.split}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    for key in dict.fromkeys((r["setup"], r["model"]) for r in rows):
        group = [r for r in rows if (r["setup"], r["model"]) == key]
        flagged = sum(1 for r in group if r["ungrounded_numbers"])
        print(f"{key[0]:9} {key[1]:18} answers with a number not from a query: {flagged:3}/{len(group)} ({flagged / len(group):.0%})")
    print(f"\nsaved: {path}")


if __name__ == "__main__":
    main()
