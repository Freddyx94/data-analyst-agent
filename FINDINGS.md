# Findings

Work in progress. Every number here comes from a file in `results/`.
So far: development questions only. The test questions have not been run, so none of these numbers is final.

## Results on the development set

36 questions, each asked 3 times per setup.

| Setup | Model | Execution accuracy (mean) | Range over 3 runs | Cost per question | Time per question | Model calls per question |
|---|---|---|---|---|---|---|
| Baseline: one prompt with the full schema, one SQL query, no tools | claude-haiku-5-5 | 92.6% | 91.7% to 94.4% | $0.00048 | 1.9 s | 1.0 |
| Agent v1: the first agent (Stage 3) | claude-haiku-5-5 | 75.9% | 72.2% to 77.8% | $0.00140 | 4.7 s | 3.8 |
| Agent v1 with a stronger model | claude-sonnet-5-5 | 88.0% | 86.1% to 91.7% | $0.03100 | 7.6 s | 4.5 |
| Agent v2: hands in its final query with a `submit_answer` tool | claude-haiku-5-5 | 80.6% | 72.2% to 86.1% | $0.00165 | 4.8 s | 3.7 |
| Agent v3: v2 plus a check when the answer is handed in | claude-haiku-5-5 | 96.3% | 91.7% to 100.0% | $0.00209 | 5.8 s | 4.4 |
| Agent v4: v3 with `sql` required in the tool and queries compared by their parsed form | claude-haiku-5-5 | 99.1% | 97.2% to 100.0% | $0.00207 | 5.8 s | 4.3 |

Source: `results/summary_dev.csv`, built from the `results/eval_*_dev.csv` files.

**Execution accuracy** means: the result of the final SQL query matches the result of a hand-written correct query. Extra columns are allowed, the number of rows must be equal, whole numbers must be exact, decimals may be rounded by less than 0.5%. For questions the data cannot answer, the correct behaviour is to decline. The rules are in `evals/metrics.py`.

The agent variants are defined in `config.yaml` (`agent.variants`), so each one can be run again exactly.

## Stage 3: the first agent against the baseline

**The first agent lost on the strict metric because of one defect.** It ended with a breakdown table and picked the answer out of it in the text, although the prompt asked for a final query that returns only the answer. Its text answer was correct in 107 of 108 runs (Haiku) and 108 of 108 (Sonnet). The baseline's failures, in contrast, are wrong or missing answers.

**Looking at the data helped where expected.** All agents declined every question the data cannot answer, including one about an item that does not exist. The baseline cannot see that the item is missing and failed that question in all 3 runs.

**The baseline is stronger than expected on stored values.** It answered most questions about named events by knowing the holiday's date or by searching with a pattern.

## Stage 4: improving the agent, one measured change at a time

| Change | Idea | Effect on the development set |
|---|---|---|
| v2: `submit_answer` tool | The agent names its final query itself, instead of the evaluation guessing "the last query that ran". | 75.9% to 80.6%. The tool was used in 107 of 108 runs, but 16 runs still handed in a wide query, and 4 handed in no query. |
| v3: check at hand-in | The loop counts the rows of the handed-in query. A query with several rows is sent back once with a question; an answer without a query is rejected. | 80.6% to 96.3%. New failure: 3 runs got stuck handing in and ran out of steps. |
| v4: required `sql`, parsed matching | `sql` is a required input in the tool schema (an empty text means "decline"). Queries are compared after sqlglot writes them in one standard form. | 96.3% to 99.1%. No run got stuck. |

What each change taught:

- **Making the output explicit was not enough** (v2). The model used the new tool, but kept handing in the same wide tables.
- **A mechanical check worked where instructions did not** (v3). The rule "return only the answer" was in the prompt since v1. A check in code that asks back at the moment of handing in fixed most cases.
- **Schema rules work better than error messages** (v4). After an error saying "sql is missing", the model repeated the same call up to 5 times. Making `sql` a required input removed the problem.

Remaining failure of v4 (1 of 108 runs): "average units per day" computed per item per day (10.4% instead of 10.3%), the same misreading the baseline made in all 3 of its runs.

## What the failures are

I read every failed run. Source: `results/failure_analysis_dev.csv`. Each setup has 108 runs.

| Failure type | Baseline | v1 Haiku | v1 Sonnet | v2 | v3 | v4 |
|---|---|---|---|---|---|---|
| Final query is not the answer (the text answer is correct) | 0 | 25 | 13 | 16 | 0 | 0 |
| Handed in a correct answer without its query | 0 | 0 | 0 | 4 | 0 | 0 |
| Got stuck handing in, ran out of steps | 0 | 0 | 0 | 0 | 3 | 0 |
| Misunderstood term ("per day" read as "per item per day") | 3 | 1 | 0 | 0 | 0 | 1 |
| Chose a meaning the gold does not cover ("last year" as the last 12 months) | 0 | 0 | 0 | 1 | 1 | 0 |
| Could not check the data (asked about an item that does not exist) | 3 | 0 | 0 | 0 | 0 | 0 |
| Guessed a stored value (event name spelled differently) | 1 | 0 | 0 | 0 | 0 | 0 |
| Gave up on a question that can be answered | 1 | 0 | 0 | 0 | 0 | 0 |
| **All failures** | **8** | **26** | **13** | **21** | **4** | **1** |

## A problem in the measurement, found and fixed

Running the same evaluation twice gave different numbers: v1 moved from 75.0% to 75.9% on a pure replay. The cause was the database, not the model. DuckDB runs on several threads, and rows that SQL leaves unordered (no ORDER BY, or ties) came back in a different order each time. The model then saw a different tool result, the cache missed, and the run was a new sample.

The connection now uses one thread (`src/agent/db.py`). Since then every replay gives identical numbers at no cost, and a test checks it. All Haiku numbers above were measured after the fix. The Sonnet run was measured before it; I have not run it again ($3.35).

## Limits of this measurement

- 36 questions is a small set. One question is 2.8 percentage points, and the ranges of v3, v4 and the baseline overlap.
- **All of Stage 4 was tuned on these development questions.** I looked at their failures to design each change, so 99.1% is an optimistic number. Only the test set can say how much of the gain is real.
- The questions and gold queries were written for this project, by the same people who built the agent.
- The failure types and the "text answer is correct" counts come from reading the runs by hand.

## Not measured yet

- The test questions.
- A local model (Ollama).
- The stronger model with the improved agent.
