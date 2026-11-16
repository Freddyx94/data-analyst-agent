"""Tests for list_tables, describe_table, sample_rows and the schema document."""

from __future__ import annotations

from agent.config import load_config
from agent.schema_doc import load_schema_doc
from agent.tools import describe_table, list_tables, run_sql, sample_rows


def test_list_tables_shows_only_allowed_tables(con):
    result = list_tables(con)
    for table in load_config()["sql"]["allowed_tables"]:
        assert table in result
    assert "secret_notes" not in result
    assert "marts.dim_item (4 rows)" in result


def test_describe_table_merges_types_and_descriptions(con):
    result = describe_table(con, "marts.dim_date")
    assert "is_snap | BOOLEAN | " in result
    assert "SNAP benefits" in result  # comes from docs/schema.md


def test_describe_table_accepts_name_without_schema(con):
    assert describe_table(con, "dim_item").startswith("marts.dim_item")


def test_describe_unknown_or_hidden_table_returns_error(con):
    assert describe_table(con, "marts.nothing").startswith("ERROR")
    assert describe_table(con, "raw.secret_notes").startswith("ERROR")


def test_sample_rows_never_returns_more_than_five(con):
    result = sample_rows(con, "marts.fct_daily_sales", n=1000)
    assert len(result.splitlines()) == 1 + 5  # header + 5 rows


def test_sample_rows_rejects_sql_in_the_table_name(con):
    result = sample_rows(con, "marts.dim_item; DROP TABLE marts.dim_item")
    assert result.startswith("ERROR")


def test_sample_rows_hidden_table_returns_error(con):
    result = sample_rows(con, "raw.secret_notes")
    assert result.startswith("ERROR")
    assert "never see this" not in result


def test_schema_doc_describes_every_allowed_table_and_column(con):
    """docs/schema.md must stay in step with the database and config.yaml."""
    docs = load_schema_doc()
    for table in load_config()["sql"]["allowed_tables"]:
        assert table in docs, f"{table} has no section in docs/schema.md"
        assert docs[table].description
        schema, name = table.split(".")
        columns = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, name],
        ).fetchall()
        for (column,) in columns:
            assert docs[table].columns.get(column), f"{table}.{column} has no description"


def test_same_query_gives_the_same_result_text_every_time(con):
    """Without a fixed order, tool results change between runs and the cache misses."""
    unordered = (
        "SELECT f.item_id, f.date, sum(f.units_sold) AS units "
        "FROM marts.fct_daily_sales f GROUP BY f.item_id, f.date"
    )
    assert len({run_sql(con, unordered) for _ in range(10)}) == 1
    assert con.execute("SELECT current_setting('threads')").fetchone()[0] == 1
