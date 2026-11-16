# Findings

Work in progress (Stages 5 and 6 are not done). Every number here comes from a file in `results/`.

## Final results (test set)

24 questions that were never used to develop the agent, each asked 3 times per setup, run once on 2026-11-17. All three setups use `claude-haiku-5-5` with prompt caching.

| Setup | Execution accuracy (mean) | Range over 3 runs | Cost per question | Time per question | Model calls per question |
|---|---|---|---|---|---|
| Baseline: one prompt with the full schema, one SQL query, no tools | 94.4% | 91.7% to 95.8% | $0.00018 | 1.5 s | 1.0 |
| First agent (v1) | 75.0% | 75.0% to 75.0% | $0.00055 | 3.8 s | 3.6 |
| Improved agent (v4) | 91.7% | 91.7% to 91.7% | $0.00082 | 5.1 s | 4.5 |

Source: `results/summary_test.csv`, built from the three `results/eval_*_test.csv` files. Failures: `results/failure_analysis_test.csv`.

**The answer to the project's question.** On these questions, an agent that can look at the data and correct itself is not more accurate than a single "question in, SQL out" prompt. It costs about 4.6 times as much per question and takes about 3.4 times as long. The changes of Stage 4 did help the agent: from 75.0% to 91.7%.

**The two approaches fail differently.**

| Failure type (test set, of 72 runs each) | Baseline | Agent v1 | Agent v4 |
|---|---|---|---|
| Final query is not the answer (the text answer is correct) | 0 | 15 | 0 |
| Answered without running a query (the text answer is correct) | 0 | 2 | 0 |
| Calculated a number in the text instead of in SQL (the text answer is about right) | 0 | 1 | 3 |
| Correct answer in a format the gold does not accept (month as `2015-05`) | 0 | 0 | 3 |
| Could not check the data (asked about an item that does not exist) | 3 | 0 | 0 |
| Guessed a stored value (event name spelled differently) | 1 | 0 | 0 |
| **All failures** | **4** | **18** | **6** |

- The baseline fails where it would need to look at the data. It cannot see that an item does not exist, so it returns an empty result instead of saying so.
- The improved agent never fails for that reason. All 6 of its failures are in how it hands over a correct answer: it calculated a percentage in its text instead of in its query (3 runs), and it wrote a month in a form the test question does not accept (3 runs). In all 6 failed runs the text answer is correct or, for the percentage, rounded from the correct value.
- The month format is a gap in a test question I wrote. I did not change the test question after seeing the result: that would tune the measurement on the test set. Under the agreed rules these 3 runs are failures.

**The development set overstated the gain.** The improved agent scored 99.1% on the development questions and 91.7% on the test questions. Every Stage 4 change was designed by looking at development failures, so this drop is expected; it is why the test set exists. The baseline scored 92.6% and 94.4%.

**Small numbers.** With 24 questions, one question is 4.2 percentage points. The difference between the baseline and the improved agent is less than one question on average.

**Cost note.** Costs on the test set include prompt caching (added after the development runs), so they are lower than the development costs below and cannot be compared with them directly.

## Results on the development set

36 questions, each asked 3 times per setup. These questions were used to develop the agent.

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

## Prompt caching (Stage 4, cost)

Most of the agent's cost was input: every step sends the whole conversation again (77% of the cost of v4 on the development set). Prompt caching lets the API read a part of a request that was sent recently at a tenth of the input price. The code marks two points (`add_cache_markers` in `src/agent/llm.py`): the end of the system prompt, shared by all questions, and the end of each request, shared by the next step. It does not change what the model sees, so the agent's behaviour stays the same.

In a live check on two questions, from step 2 on almost all input was read from the cache, and the two questions cost $0.0007 and $0.0009. On the test set, the improved agent cost $0.00082 per question.

## Limits of this measurement

- The sets are small: 36 development and 24 test questions. One test question is 4.2 percentage points.
- All of Stage 4 was tuned on the development questions, so the development numbers of v2 to v4 are optimistic.
- The questions and gold queries were written for this project, by the same people who built the agent.
- The failure types and the "text answer is correct" counts come from reading the runs by hand.
- Only one model (`claude-haiku-5-5`) was used for the final numbers.

## Not measured

- A local model (Ollama).
- The stronger model (`claude-sonnet-5-5`) with the improved agent. Left out to stay within the remaining budget.
