"""One test per guardrail of run_sql.

Each test sends something the agent must not be able to do and checks two
things: the tool answers with an ERROR text (it does not crash), and the data
is unchanged afterwards.
"""

from __future__ import annotations

import time

import duckdb
import pytest

from agent.tools import run_sql


def _item_count(con: duckdb.DuckDBPyConnection) -> int:
    return con.execute("SELECT count(*) FROM marts.dim_item").fetchone()[0]


def test_valid_select_returns_rows(con):
    result = run_sql(con, "SELECT item_id FROM marts.dim_item ORDER BY item_id")
    assert result.splitlines()[0] == "item_id"
    assert "FOODS_1_001" in result
    assert result.endswith("(4 rows)")


def test_cte_and_union_are_allowed(con):
    sql = """
        WITH totals AS (
            SELECT item_id, sum(units_sold) AS units FROM marts.fct_daily_sales GROUP BY item_id
        )
        SELECT item_id, units FROM totals
        UNION ALL
        SELECT 'all', sum(units) FROM totals
    """
    assert not run_sql(con, sql).startswith("ERROR")


def test_trailing_semicolon_is_allowed(con):
    assert not run_sql(con, "SELECT 1 AS one;").startswith("ERROR")


def test_drop_is_rejected(con):
    result = run_sql(con, "DROP TABLE marts.dim_item")
    assert result.startswith("ERROR")
    assert "DROP" in result and "SELECT" in result
    assert _item_count(con) == 4


def test_delete_is_rejected(con):
    result = run_sql(con, "DELETE FROM marts.dim_item")
    assert result.startswith("ERROR")
    assert "DELETE" in result
    assert _item_count(con) == 4


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO marts.dim_store VALUES ('X', 'Y')",
        "UPDATE marts.dim_item SET dept_id = 'X'",
        "CREATE TABLE marts.new_table (i INTEGER)",
        "ALTER TABLE marts.dim_item ADD COLUMN x INTEGER",
        "COPY marts.dim_item TO 'items.csv'",
        "ATTACH 'other.duckdb'",
        "PRAGMA database_list",
        "SET enable_external_access = true",
    ],
)
def test_other_non_select_statements_are_rejected(con, sql):
    assert run_sql(con, sql).startswith("ERROR")


def test_two_statements_are_rejected(con):
    result = run_sql(con, "SELECT 1; DROP TABLE marts.dim_item")
    assert result.startswith("ERROR")
    assert "exactly one" in result
    assert _item_count(con) == 4


def test_empty_input_is_rejected(con):
    assert run_sql(con, "   ").startswith("ERROR")


def test_huge_result_is_cut_at_the_row_limit(con):
    result = run_sql(con, "SELECT * FROM range(100000)", max_rows=50)
    lines = result.splitlines()
    assert len(lines) == 1 + 50 + 1  # header + rows + note
    assert "first 50 rows" in lines[-1]


def test_slow_query_is_cancelled(con):
    # A join without a condition: 10^14 row pairs, far too many to finish.
    slow = "SELECT sum(a.range * b.range) FROM range(10000000) a, range(10000000) b"
    start = time.monotonic()
    result = run_sql(con, slow, timeout_seconds=0.5)
    elapsed = time.monotonic() - start
    assert result.startswith("ERROR")
    assert "cancelled" in result
    assert elapsed < 5
    # The connection must still work after a cancelled query.
    assert run_sql(con, "SELECT 1 AS one").endswith("(1 rows)")


def test_table_outside_the_allowed_list_is_rejected(con):
    result = run_sql(con, "SELECT * FROM raw.secret_notes")
    assert result.startswith("ERROR")
    assert "never see this" not in result


def test_model_input_table_stays_hidden_but_stockout_days_is_visible(con):
    """ml.stockout_days is allowed; ml.feat_daily_sales in the same schema is not."""
    assert run_sql(con, "SELECT * FROM ml.stockout_days").endswith("(2 rows)")
    hidden = run_sql(con, "SELECT * FROM ml.feat_daily_sales")
    assert hidden.startswith("ERROR")
    assert "99.5" not in hidden


def test_hidden_table_inside_a_subquery_is_rejected(con):
    sql = "SELECT * FROM marts.dim_store WHERE store_id IN (SELECT note FROM raw.secret_notes)"
    assert run_sql(con, sql).startswith("ERROR")


def test_system_tables_are_rejected(con):
    assert run_sql(con, "SELECT * FROM information_schema.tables").startswith("ERROR")


@pytest.mark.parametrize(
    "sql",
    ["SELECT * FROM read_csv('/etc/hosts')", "SELECT * FROM '/etc/hosts'"],
)
def test_reading_files_is_rejected(con, sql):
    assert run_sql(con, sql).startswith("ERROR")


def test_missing_schema_gets_a_helpful_message(con):
    result = run_sql(con, "SELECT * FROM dim_item")
    assert result.startswith("ERROR")
    assert "marts.dim_item" in result


def test_syntax_error_is_returned_as_text(con):
    result = run_sql(con, "SELEC item_id FROM")
    assert result.startswith("ERROR")
    assert "parsed" in result


def test_database_error_is_returned_as_text(con):
    result = run_sql(con, "SELECT no_such_column FROM marts.dim_item")
    assert result.startswith("ERROR")
    assert "no_such_column" in result


def test_connection_itself_refuses_writes_and_file_access(con):
    """The lowest layer: even without the SQL check, DuckDB says no."""
    with pytest.raises(duckdb.Error):
        con.execute("DROP TABLE marts.dim_item")
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM read_csv('/etc/hosts')")
    assert _item_count(con) == 4
