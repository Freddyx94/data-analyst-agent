"""Tests for the one-off setup script. No network: the CSV text is written here."""

from __future__ import annotations

import duckdb
import pytest

from agent.db import connect
from agent.tools import describe_table, run_sql
from load_holdout_results import EXPECTED_COLUMNS, load, parse

HEADER = ",".join(EXPECTED_COLUMNS)
GOOD_CSV = (
    f"{HEADER}\n"
    "model_a,1.5,2.5,0.75,0.77,-0.07,0.76,-0.07,26.0,3.0,1.5\n"
    "model_b,1.8,3.5,1.02,1.08,-0.07,1.03,-0.07,0.0,-31.0,-33.0\n"
)


def test_parse_reads_model_name_and_numbers():
    rows = parse(GOOD_CSV)
    assert len(rows) == 2
    assert rows[0][0] == "model_a"
    assert rows[0][3] == 0.75
    assert all(isinstance(value, float) for value in rows[0][1:])


def test_parse_rejects_an_unexpected_header():
    """Column names from a downloaded file must not reach SQL unchecked."""
    bad = GOOD_CSV.replace("rmsse_clean", "rmsse_clean); DROP TABLE marts.dim_item; --")
    with pytest.raises(ValueError, match="Unexpected CSV header"):
        parse(bad)


def test_parse_rejects_text_where_a_number_is_expected():
    with pytest.raises(ValueError):
        parse(GOOD_CSV.replace("2.5", "not a number"))


def test_parse_rejects_an_empty_file():
    with pytest.raises(ValueError):
        parse(f"{HEADER}\n")


def test_load_creates_a_table_the_agent_tools_can_read(tmp_path):
    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()  # an empty database file

    load(parse(GOOD_CSV), db_path)
    load(parse(GOOD_CSV), db_path)  # running twice must not duplicate rows

    con = connect(db_path)
    result = run_sql(
        con,
        "SELECT model, rmsse_better_than_ets_pct FROM results.holdout_results ORDER BY model",
    )
    con.close()
    assert "model_a | 1.5" in result
    assert result.endswith("(2 rows)")


def test_fixture_has_the_same_columns_as_the_loader_creates(con):
    """The % in the CSV names is replaced by pct, so no quoting is needed."""
    described = describe_table(con, "results.holdout_results")
    assert "rmsse_better_than_seasonal_naive_pct | DOUBLE" in described
    column_lines = described.split("\n\n", 1)[1].splitlines()[1:]
    assert all("%" not in line.split(" | ")[0] for line in column_lines)
