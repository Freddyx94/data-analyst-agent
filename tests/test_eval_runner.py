"""Tests for the evaluation runner: baseline, scoring, cache, cost limit, summary.

All of them use a fake model and the fixture database: no key, no cost.
"""

from __future__ import annotations

import pytest

from agent.llm import CachedModel, FakeModel, ModelReply, ToolCall
from evals.questions import Question
from evals.run import ResultRow, evaluate, score, summarize
from evals.setups import Outcome, build_baseline_prompt, extract_sql, run_baseline


class AlwaysSameModel:
    """A model that gives the same reply to everything and counts its calls."""

    name = "always-same"

    def __init__(self, text: str, cost_usd: float = 0.0) -> None:
        self.text = text
        self.cost_usd = cost_usd
        self.calls = 0

    def reply(self, system, messages, tools) -> ModelReply:
        self.calls += 1
        return ModelReply(text=self.text, input_tokens=10, output_tokens=5, cost_usd=self.cost_usd, seconds=0.5)


def _outcome(answer="", final_sql=None, stopped="answer") -> Outcome:
    return Outcome(answer, final_sql, stopped, steps=1, input_tokens=0, output_tokens=0,
                   cost_usd=0.0, spent_usd=0.0, seconds=0.0)


ITEMS = Question(id="q", level="lookup", question="How many items?", gold_sql=["SELECT count(*) FROM marts.dim_item"])
UNANSWERABLE = Question(id="u", level="unanswerable", question="How many returns?")
GOLD_ITEMS = [[(4,)]]  # the fixture has 4 items

# --- baseline ------------------------------------------------------------------


def test_sql_is_taken_out_of_the_code_block():
    assert extract_sql("```sql\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("Here you go:\n```\nSELECT 2\n```\nDone.") == "SELECT 2"
    assert extract_sql("SELECT 3 FROM marts.dim_item") == "SELECT 3 FROM marts.dim_item"
    assert extract_sql("with x as (select 1) select * from x").startswith("with")
    assert extract_sql("I am not sure what you mean.") is None


def test_baseline_prompt_contains_every_table_and_no_placeholder(con):
    prompt = build_baseline_prompt(con)
    assert "{schema}" not in prompt and "{general_notes}" not in prompt
    for table in ["marts.fct_daily_sales", "ml.stockout_days", "results.holdout_results"]:
        assert table in prompt
    assert "SNAP benefits" in prompt
    assert "feat_daily_sales" not in prompt  # the hidden table stays hidden


def test_baseline_makes_one_call_without_tools(con):
    model = FakeModel([ModelReply(text="```sql\nSELECT count(*) FROM marts.dim_item\n```")])
    outcome = run_baseline("How many items?", model, con)
    assert outcome.final_sql == "SELECT count(*) FROM marts.dim_item"
    assert outcome.steps == 1
    assert model.calls[0]["tools"] == []


def test_baseline_decline_word_becomes_a_decline(con):
    outcome = run_baseline("How many returns?", FakeModel([ModelReply(text="CANNOT_ANSWER")]), con)
    assert outcome.final_sql is None
    assert score(UNANSWERABLE, outcome, [], con) == (True, "")


# --- scoring: one case per failure type -----------------------------------------


def test_correct_result(con):
    outcome = _outcome("There are 4 items.", "SELECT count(*) AS n, 'extra' AS label FROM marts.dim_item")
    assert score(ITEMS, outcome, GOLD_ITEMS, con) == (True, "")


def test_wrong_values(con):
    outcome = _outcome("5 items.", "SELECT count(*) + 1 FROM marts.dim_item")
    assert score(ITEMS, outcome, GOLD_ITEMS, con) == (False, "wrong_values")


def test_wrong_row_count(con):
    outcome = _outcome("Here are the items.", "SELECT item_id FROM marts.dim_item")
    assert score(ITEMS, outcome, GOLD_ITEMS, con) == (False, "wrong_row_count")


def test_wrong_order(con):
    question = Question(id="o", level="aggregation", question="Items, A to Z?", ordered=True,
                        gold_sql=["SELECT item_id FROM marts.dim_item ORDER BY item_id"])
    gold = [[("FOODS_1_001",), ("FOODS_2_001",), ("FOODS_3_001",), ("FOODS_3_002",)]]
    outcome = _outcome("…", "SELECT item_id FROM marts.dim_item ORDER BY item_id DESC")
    assert score(question, outcome, gold, con) == (False, "wrong_order")


def test_gave_up_on_an_answerable_question(con):
    assert score(ITEMS, _outcome("I can't answer that from this data."), GOLD_ITEMS, con) == (False, "gave_up")


def test_answered_an_unanswerable_question(con):
    outcome = _outcome("There were 0 returns.", "SELECT 0")
    assert score(UNANSWERABLE, outcome, [], con) == (False, "answered_unanswerable")


def test_sql_error_no_query_and_max_steps(con):
    assert score(ITEMS, _outcome("…", "SELECT nope FROM marts.dim_item"), GOLD_ITEMS, con) == (False, "sql_error")
    assert score(ITEMS, _outcome("…", "DROP TABLE marts.dim_item"), GOLD_ITEMS, con) == (False, "sql_error")
    assert score(ITEMS, _outcome("About 4, I think."), GOLD_ITEMS, con) == (False, "no_query")
    assert score(ITEMS, _outcome("…", "SELECT 4", stopped="max_steps"), GOLD_ITEMS, con) == (False, "max_steps")


# --- cache -----------------------------------------------------------------------


def test_cache_returns_the_stored_reply_without_calling_the_model(tmp_path):
    inner = AlwaysSameModel("hello", cost_usd=0.01)
    cached = CachedModel(inner, tmp_path, salt="run1")
    messages = [{"role": "user", "text": "question"}]

    first = cached.reply("system", messages, [])
    second = cached.reply("system", messages, [])

    assert inner.calls == 1
    assert (first.cached, second.cached) == (False, True)
    assert second.text == "hello" and second.cost_usd == 0.01 and second.seconds == 0.5


def test_cache_key_changes_with_the_request_and_the_run(tmp_path):
    inner = AlwaysSameModel("hello")
    messages = [{"role": "user", "text": "question"}]
    CachedModel(inner, tmp_path, salt="run1").reply("system", messages, [])
    CachedModel(inner, tmp_path, salt="run2").reply("system", messages, [])        # another run
    CachedModel(inner, tmp_path, salt="run1").reply("other system", messages, [])  # another prompt
    CachedModel(inner, tmp_path, salt="run1").reply("system", [{"role": "user", "text": "other"}], [])
    assert inner.calls == 4


def test_cache_keeps_tool_calls(tmp_path):
    reply = ModelReply(tool_calls=[ToolCall("c1", "run_sql", {"sql": "SELECT 1"})])
    cached = CachedModel(FakeModel([reply]), tmp_path)
    messages = [{"role": "user", "text": "q"}]
    cached.reply("s", messages, [])
    again = cached.reply("s", messages, [])
    assert again.tool_calls == [ToolCall("c1", "run_sql", {"sql": "SELECT 1"})]


# --- the whole evaluation ---------------------------------------------------------


def test_evaluate_scores_every_question_in_every_run(con, tmp_path):
    """A model that always declines: right on the 5 unanswerable questions, wrong on the other 31."""
    model = AlwaysSameModel("CANNOT_ANSWER")
    rows, spent, stopped_early = evaluate("baseline", model, "dev", 2, con, budget_usd=1.0, cache_dir=tmp_path)

    assert not stopped_early and spent == 0.0
    assert len(rows) == 72
    assert sum(r.correct for r in rows) == 10
    assert {r.failure_type for r in rows if not r.correct} == {"gave_up"}

    summary = summarize(rows)
    assert summary["accuracy_mean"] == pytest.approx(5 / 36)
    assert summary["accuracy_min"] == summary["accuracy_max"]
    assert summary["seconds_per_question"] == pytest.approx(0.5)


def test_second_evaluation_is_free_because_of_the_cache(con, tmp_path):
    model = AlwaysSameModel("CANNOT_ANSWER", cost_usd=0.001)
    _, spent_first, _ = evaluate("baseline", model, "dev", 1, con, budget_usd=1.0, cache_dir=tmp_path)
    calls_after_first = model.calls
    rows, spent_second, _ = evaluate("baseline", model, "dev", 1, con, budget_usd=1.0, cache_dir=tmp_path)

    assert spent_first == pytest.approx(0.036)
    assert spent_second == 0.0
    assert model.calls == calls_after_first
    assert rows[0].cost_usd == 0.001  # the list price is still reported


def test_evaluation_stops_at_the_spending_limit(con, tmp_path):
    model = AlwaysSameModel("CANNOT_ANSWER", cost_usd=0.01)
    rows, spent, stopped_early = evaluate("baseline", model, "dev", 1, con, budget_usd=0.05, cache_dir=tmp_path)
    assert stopped_early
    assert len(rows) == 5
    assert spent == pytest.approx(0.05)


def test_summary_reports_mean_and_range_over_runs():
    def row(run, correct):
        return ResultRow("agent", "m", "p", "dev", run, "q", "lookup", correct, "", 2, 0, 0, 0.002, 4.0, "", "")

    rows = [row(1, True), row(1, True), row(2, True), row(2, False)]
    summary = summarize(rows)
    assert summary["accuracy_mean"] == pytest.approx(0.75)
    assert (summary["accuracy_min"], summary["accuracy_max"]) == (0.5, 1.0)
    assert summary["cost_per_question_usd"] == pytest.approx(0.002)
    assert summary["steps_per_question"] == 2


def test_final_query_is_stored_with_its_line_breaks(con, tmp_path):
    """A query starting with a '-- comment' line must still run after it is saved and read back."""
    from evals.run import save_rows
    from evals.summary import read_rows

    sql = "-- count the items\nSELECT count(*) FROM marts.dim_item"
    model = AlwaysSameModel(f"```sql\n{sql}\n```")
    rows, _, _ = evaluate("baseline", model, "dev", 1, con, budget_usd=1.0, cache_dir=tmp_path / "cache")
    save_rows(rows, tmp_path / "eval.csv")
    stored = read_rows(tmp_path / "eval.csv")[0].final_sql
    assert stored == sql
    assert con.execute(stored).fetchone()[0] == 4
