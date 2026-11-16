"""Hand-made examples for the number check."""

from __future__ import annotations

from agent.grounding import extract_numbers, is_rounding_of, ungrounded_numbers
from agent.llm import FakeModel, ModelReply, ToolCall
from agent.loop import run_agent

RESULT = "forecast_total | actual\n92986.2313 | 90833\n(1 rows)"


def test_a_percentage_calculated_in_the_text_is_flagged():
    """The real test-set case: the query returned two totals, the answer adds a percentage."""
    answer = "The forecast (92,986 units) is about 2.4% higher than actual sales (90,833 units)."
    assert ungrounded_numbers(answer, [RESULT]) == ["2.4"]


def test_honest_rounding_is_accepted():
    sources = ["revenue\n251336.06\n(1 rows)", "units\n1055433\n(1 rows)", "pct\n8.6414\n(1 rows)"]
    answer = "Revenue was $251,336, from about 1.06 million units, up 8.6% (8.64% exactly)."
    assert ungrounded_numbers(answer, sources) == []


def test_rounding_too_far_is_flagged():
    assert ungrounded_numbers("about 3 million units", ["units\n2788087\n(1 rows)"]) == ["3 million"]


def test_numbers_from_the_question_and_from_the_sql_are_allowed():
    question = "How many units were sold in 2015?"
    sql = "SELECT sum(units_sold) FROM marts.fct_daily_sales WHERE date >= DATE '2014-06-01'"
    answer = "In 2015, and since 2014-06-01, 1,055,433 units were sold."
    assert ungrounded_numbers(answer, [question, sql, "units\n1055433\n(1 rows)"]) == []


def test_days_in_dates_identifiers_and_small_numbers_are_not_checked():
    answer = "From 1 January to 31 December, item FOODS_3_090 was in the top 5 of 3 departments."
    assert ungrounded_numbers(answer, []) == []
    assert ungrounded_numbers("Jan 87,988 and Feb 79,166", []) == ["87,988", "79,166"]


def test_extract_numbers_reads_separators_and_scale_words():
    values = [v for _, v in extract_numbers("1,055,433 units, $2.79 million, 10.3%, 4 thousand")]
    assert values == [1055433, 2790000, 10.3, 4000]
    assert extract_numbers("FOODS_3_090 d_17 CA_1") == []


def test_is_rounding_of():
    assert is_rounding_of(2.6130, 2.61)
    assert is_rounding_of(1055433, 1060000)
    assert not is_rounding_of(0.7543, 0.7565)
    assert not is_rounding_of(2788087, 3000000)  # 7.6% off


def test_the_loop_records_ungrounded_numbers(con):
    sql = "SELECT count(*) AS items FROM marts.dim_item"
    model = FakeModel([
        ModelReply(tool_calls=[ToolCall("a", "run_sql", {"sql": sql})]),
        ModelReply(tool_calls=[ToolCall("b", "submit_answer", {"answer": "There are 4 items, about 37.5% of 2015's range.", "sql": sql})]),
    ])
    result = run_agent("How many items?", model, con, variant="v4")
    assert result.submitted
    assert result.ungrounded_numbers == ["37.5", "2015"]  # 4 is small; 37.5 and 2015 appear nowhere in the run


def test_a_clean_answer_has_no_ungrounded_numbers(con):
    sql = "SELECT sum(units_sold) AS units FROM marts.fct_daily_sales"
    total = con.execute(sql).fetchone()[0]
    model = FakeModel([
        ModelReply(tool_calls=[ToolCall("a", "run_sql", {"sql": sql})]),
        ModelReply(tool_calls=[ToolCall("b", "submit_answer", {"answer": f"{total:,} units were sold.", "sql": sql})]),
    ])
    assert run_agent("How many units?", model, con, variant="v4").ungrounded_numbers == []
