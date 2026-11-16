# Findings

Work in progress. Every number here comes from a file in `results/`.
So far: Stage 3, the first measurement, on the development questions only. The test questions have not been run, and the agent has not been improved yet.

## Results on the development set

36 questions, each asked 3 times per setup.

| Setup | Model | Execution accuracy (mean) | Range over 3 runs | Cost per question | Time per question | Model calls per question |
|---|---|---|---|---|---|---|
| Baseline: one prompt with the full schema, one SQL query, no tools | claude-haiku-5-5 | 92.6% | 91.7% to 94.4% | $0.00048 | 1.9 s | 1.0 |
| Agent: looks at the schema with tools, runs queries, can retry | claude-haiku-5-5 | 75.0% | 72.2% to 77.8% | $0.00140 | 4.7 s | 3.8 |
| Agent with a stronger model | claude-sonnet-5-5 | 88.0% | 86.1% to 91.7% | $0.03100 | 7.6 s | 4.5 |

Source: `results/summary_dev.csv`, built from the three `results/eval_*_dev.csv` files.

**Execution accuracy** means: the result of the final SQL query matches the result of a hand-written correct query. Extra columns are allowed, the number of rows must be equal, whole numbers must be exact, decimals may be rounded by less than 0.5%. For questions the data cannot answer, the correct behaviour is to decline. The rules are in `evals/metrics.py`.

A local model (Ollama) was planned as a fourth setup and has not been run.

## What the failures are

I read every failed run. Source: `results/failure_analysis_dev.csv`. Each setup has 108 runs.

| Failure type | Baseline (Haiku) | Agent (Haiku) | Agent (Sonnet) |
|---|---|---|---|
| Final query is not the answer: the text answer is correct, but the last query returns a wider table (all weekdays, all departments, a top 5) | 0 | 26 | 13 |
| Misunderstood term: "average units per day" computed as average per item per day | 3 | 1 | 0 |
| Could not check the data: asked about an item that does not exist, wrote a normal query that returns nothing | 3 | 0 | 0 |
| Guessed a stored value: filtered on an event name that is spelled differently in the data | 1 | 0 | 0 |
| Gave up on a question that can be answered | 1 | 0 | 0 |
| SQL that does not run, invented numbers, ran out of steps | 0 | 0 | 0 |
| **All failures** | **8** | **27** | **13** |

There are two different things in this table.

**The agent's failures are almost all one defect.** Its text answer states the correct value in 107 of 108 runs with Haiku and in all 108 with Sonnet. It still fails the metric because it ends with a breakdown table and picks the answer out of it in the text, although the system prompt asks for a final query that returns only the answer. This counts as a failure for a reason: the app will show the last query's result next to the answer, and the two should say the same thing.

**The baseline's failures are wrong answers.** Its query is correct in 100 of 108 runs. In the other 8 the answer itself is wrong or missing.

Accuracy by question level (correct runs out of all runs):

| Level | Baseline (Haiku) | Agent (Haiku) | Agent (Sonnet) |
|---|---|---|---|
| Simple lookup | 12/12 | 12/12 | 12/12 |
| Aggregation | 15/15 | 14/15 | 13/15 |
| Joins | 15/15 | 9/15 | 12/15 |
| Time windows | 15/15 | 15/15 | 15/15 |
| Multi-step | 9/12 | 4/12 | 6/12 |
| Ambiguous | 9/9 | 3/9 | 9/9 |
| Data lookup (the stored value cannot be guessed) | 13/15 | 9/15 | 13/15 |
| Unanswerable | 12/15 | 15/15 | 15/15 |

## What this means so far

- **On the strict metric the simple approach wins**, at about a third of the agent's cost with the same model, and about one sixty-fifth of the cost of the agent with Sonnet.
- **The agent finds the right answer more often than the baseline, but does not hand it over in the required form.** One defect (the final query) hides this in the accuracy number. It is the first thing to fix in Stage 4.
- **Looking at the data helps exactly where expected.** Both agents declined all 15 unanswerable runs, including the item that does not exist. The baseline cannot see that the item is missing and failed that question in all 3 runs.
- **The baseline is stronger than expected on stored values.** It answered most event questions by knowing the date of the holiday or by searching with a pattern, without ever seeing the data.
- **The agent's "see an error and fix it" ability was not needed.** No setup produced a final query that failed to run.
- **The stronger model follows the final-query rule more often** (13 failures instead of 26) and made no wrong answer, at 22 times the cost per question of the same agent with Haiku.

## Limits of this measurement

- 36 questions is a small set. One question is 2.8 percentage points.
- The questions and the gold queries were written for this project, by the same people who built the agent.
- The failure types and the "text answer is correct" counts come from reading the runs by hand, not from code.
- These are development questions. They will be used to improve the agent, so the final numbers must come from the test set.

## Not measured yet

- A local model (Ollama).
- The test questions.
- Any improvement to the agent (Stage 4).
