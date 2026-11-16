"""Hand-made examples for the metric: each rule has a case that must pass and one that must fail."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from evals.metrics import is_correct, is_decline, results_match, values_match

# --- single values -----------------------------------------------------------


def test_whole_numbers_must_be_exact():
    assert values_match(398, 398)
    assert values_match(398, 398.0)             # same count, different type
    assert values_match(398, Decimal("398"))
    assert not values_match(398, 399)           # off by one is wrong
    assert not values_match(1055433, 1055000)   # "about right" is wrong for a count


def test_decimal_numbers_may_be_rounded_a_little():
    assert values_match(2.6130, 2.61)             # 2 decimals, 0.1% off
    assert values_match(1918394.7265, 1918394.73)
    assert values_match(1918394.7265, 1918395)    # rounded to whole dollars
    assert values_match(11.87, 11.9)
    assert values_match(0.1 + 0.2, 0.3)           # tiny floating point noise


def test_rounding_too_much_or_a_different_value_is_wrong():
    assert not values_match(11.87, 12)        # 1.1% off: too rough
    assert not values_match(0.7657, 0.77)     # 0.6% off
    assert not values_match(0.7543, 0.7565)   # close, but a different model's score
    assert not values_match(-0.0676, -0.0672)
    assert not values_match(11.87, 0.1187)    # percent versus fraction


def test_text_dates_booleans_and_empty_values():
    assert values_match("FOODS_3", " foods_3 ")
    assert not values_match("FOODS_3", "FOODS_2")
    assert values_match(date(2012, 3, 3), "2012-03-03")
    assert values_match(date(2012, 3, 3), datetime(2012, 3, 3, 0, 0))
    assert not values_match(date(2012, 3, 3), date(2012, 3, 4))
    assert values_match(True, True) and not values_match(True, False)
    assert not values_match(True, 1)          # a flag is not a number
    assert values_match(None, None)
    assert not values_match(None, 0) and not values_match(0, None)
    assert not values_match("5", 5)           # text is not a number


# --- whole tables ------------------------------------------------------------

GOLD_DEPTS = [("FOODS_1", 100), ("FOODS_2", 200), ("FOODS_3", 300)]


def test_same_table_matches():
    assert results_match(GOLD_DEPTS, [("FOODS_1", 100), ("FOODS_2", 200), ("FOODS_3", 300)])


def test_row_order_is_ignored_unless_asked_for():
    shuffled = [("FOODS_3", 300), ("FOODS_1", 100), ("FOODS_2", 200)]
    assert results_match(GOLD_DEPTS, shuffled)
    assert not results_match(GOLD_DEPTS, shuffled, ordered=True)
    assert results_match(GOLD_DEPTS, list(GOLD_DEPTS), ordered=True)


def test_column_order_and_extra_columns_are_allowed():
    swapped_with_extra = [(100, "FOODS_1", 12), (200, "FOODS_2", 12), (300, "FOODS_3", 12)]
    assert results_match(GOLD_DEPTS, swapped_with_extra)


def test_right_numbers_next_to_the_wrong_labels_do_not_match():
    """Each column alone has the right values, but the rows pair them wrongly."""
    mixed_up = [("FOODS_1", 300), ("FOODS_2", 200), ("FOODS_3", 100)]
    assert not results_match(GOLD_DEPTS, mixed_up)


def test_missing_column_or_different_row_count_does_not_match():
    assert not results_match(GOLD_DEPTS, [("FOODS_1",), ("FOODS_2",), ("FOODS_3",)])
    assert not results_match(GOLD_DEPTS, GOLD_DEPTS[:2])
    # Gold asks for the one best department; a list of all three is not that answer.
    assert not results_match([("FOODS_3",)], [("FOODS_3", 300), ("FOODS_2", 200), ("FOODS_1", 100)])


def test_single_number_with_self_check_columns_matches():
    """Like the real trace: the agent adds row count and date range next to the total."""
    gold = [(1055433,)]
    pred = [(1055433, 521217, date(2015, 1, 1), date(2015, 12, 31))]
    assert results_match(gold, pred)
    assert not results_match(gold, [(1055432, 521217, date(2015, 1, 1), date(2015, 12, 31))])


def test_rows_with_equal_values_are_counted_properly():
    assert results_match([(5,), (5,), (7,)], [(7,), (5,), (5,)])
    assert not results_match([(5,), (5,), (7,)], [(5,), (7,), (7,)])


def test_empty_results():
    assert results_match([], [])
    assert not results_match([], [(1,)])


# --- the final verdict ---------------------------------------------------------


def test_decline_is_recognised():
    assert is_decline("I can't answer that from this data. There are no costs.")
    assert is_decline("I can’t answer that from this data.")   # curly apostrophe
    assert is_decline("I cannot answer this from the data.")
    assert not is_decline("FOODS_3 sold the most units.")


def test_unanswerable_question_is_correct_only_when_the_agent_declines():
    assert is_correct(False, [], "I can't answer that from this data.", None)
    assert not is_correct(False, [], "There were 0 returns in 2015.", [(0,)])


def test_answerable_question_needs_a_matching_result_and_no_decline():
    gold = [[(398,)]]
    assert is_correct(True, gold, "There are 398 items.", [(398,)])
    assert not is_correct(True, gold, "There are 400 items.", [(400,)])
    assert not is_correct(True, gold, "I can't answer that from this data.", [(398,)])
    assert not is_correct(True, gold, "There are 398 items.", None)  # no query ran


def test_ambiguous_question_accepts_any_listed_gold_result():
    by_units, by_revenue = [("FOODS_3_090",)], [("FOODS_3_120",)]
    assert is_correct(True, [by_units, by_revenue], "…", [("FOODS_3_120", 9.5)])
    assert not is_correct(True, [by_units, by_revenue], "…", [("FOODS_3_555",)])
