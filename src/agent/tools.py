"""The database tools the agent can use.

Why they look like this: each tool is a plain function. It takes the database
connection plus simple typed inputs and returns text, because a language model
reads and writes text. A tool never raises an error for bad input: it returns a
message starting with "ERROR:" so the model can read it and try again.

The docstring of each tool is written for the model: in Stage 2 it becomes the
tool description the model sees.
"""

from __future__ import annotations

import threading
from typing import Any

import duckdb

from agent.config import load_config
from agent.schema_doc import load_schema_doc
from agent.sql_guard import check_sql


def list_tables(con: duckdb.DuckDBPyConnection) -> str:
    """List the tables you can query, with a short description and the row count."""
    docs = load_schema_doc()
    lines = []
    for table in _allowed_tables():
        row_count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        description = docs[table].description if table in docs else "(no description)"
        lines.append(f"{table} ({row_count:,} rows): {description}")
    return "\n".join(lines)


def describe_table(con: duckdb.DuckDBPyConnection, table: str) -> str:
    """Show the columns of one table: name, type and what the column means."""
    name = _resolve_table(table)
    if name is None:
        return _unknown_table(table)

    schema, table_name = name.split(".")
    columns = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
        [schema, table_name],
    ).fetchall()
    row_count = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]

    doc = load_schema_doc().get(name)
    rows = [
        (column, data_type, doc.columns.get(column, "") if doc else "")
        for column, data_type in columns
    ]
    header = f"{name} ({row_count:,} rows)"
    description = doc.description if doc else "(no description)"
    return f"{header}\n{description}\n\n" + _format_rows(["column", "type", "description"], rows)


def sample_rows(con: duckdb.DuckDBPyConnection, table: str, n: int = 5) -> str:
    """Show a few example rows of one table (at most 5), to see what the values look like."""
    name = _resolve_table(table)
    if name is None:
        return _unknown_table(table)

    limit = max(1, min(int(n), _settings()["sample_rows"]))
    cursor = con.execute(f"SELECT * FROM {name} LIMIT {limit}")
    columns = [d[0] for d in cursor.description]
    return _format_rows(columns, cursor.fetchall())


class SqlError(Exception):
    """A query was rejected, failed or was cancelled. The message is written for the model."""


def query_rows(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    max_rows: int | None = None,
    timeout_seconds: float | None = None,
) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    """Check and run one SELECT. Return (column names, rows, was the result cut?).

    Why separate from run_sql: the model needs text, but the evaluation needs
    the real values to compare them with the gold result. Both go through this
    one function, so both get the same guardrails. Raises SqlError on any problem.
    """
    settings = _settings()
    max_rows = max_rows if max_rows is not None else settings["max_rows"]
    timeout = timeout_seconds if timeout_seconds is not None else settings["timeout_seconds"]

    problem = check_sql(sql, settings["allowed_tables"])
    if problem:
        raise SqlError(problem)

    # DuckDB has no timeout setting. A timer in a second thread cancels the
    # query through con.interrupt() if it is still running when time is up.
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        cursor = con.execute(sql)
        columns = [d[0] for d in cursor.description]
        # Read one row more than the limit, only to find out if there are more.
        rows = cursor.fetchmany(max_rows + 1)
    except duckdb.InterruptException:
        raise SqlError(
            f"The query was cancelled because it ran longer than {timeout:g} seconds. "
            "Make it cheaper: filter on date, aggregate, or avoid joins without a join condition."
        ) from None
    except duckdb.Error as error:
        # For example an unknown column. The model can fix this from the message.
        raise SqlError(f"The database could not run the query. {error}") from None
    finally:
        timer.cancel()

    return columns, rows[:max_rows], len(rows) > max_rows


def run_sql(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    max_rows: int | None = None,
    timeout_seconds: float | None = None,
) -> str:
    """Run one SELECT query and return the result as a text table.

    Only a single SELECT on the allowed tables is accepted. The result is cut
    after a maximum number of rows, and a query that runs too long is cancelled.
    """
    try:
        columns, rows, was_cut = query_rows(con, sql, max_rows, timeout_seconds)
    except SqlError as error:
        return f"ERROR: {error}"

    if was_cut:
        note = (
            f"(showing the first {len(rows)} rows; the result has more. "
            "Aggregate, filter or add LIMIT to get a smaller result.)"
        )
    else:
        note = f"({len(rows)} rows)"
    return f"{_format_rows(columns, rows)}\n{note}"


# --- helpers ---------------------------------------------------------------


def _settings() -> dict[str, Any]:
    return load_config()["sql"]


def _allowed_tables() -> list[str]:
    return [name.lower() for name in _settings()["allowed_tables"]]


def _resolve_table(table: str) -> str | None:
    """Match the input to one allowed table, or return None.

    Why: the table name goes into a SQL string, so we never use the input
    itself. We only use the matching name from our own allowed list.
    """
    wanted = table.strip().strip('"`').lower()
    allowed = _allowed_tables()
    if wanted in allowed:
        return wanted
    # Accept "dim_date" for "marts.dim_date" when it is not ambiguous.
    matches = [name for name in allowed if name.split(".")[1] == wanted]
    return matches[0] if len(matches) == 1 else None


def _unknown_table(table: str) -> str:
    return f"ERROR: Unknown table {table!r}. Available tables: {', '.join(_allowed_tables())}."


def _format_rows(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    """Format a result as lines of 'a | b | c': compact and easy for a model to read."""
    lines = [" | ".join(columns)]
    lines += [" | ".join(_format_value(value) for value in row) for row in rows]
    return "\n".join(lines)


def _format_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        # 4 decimals is enough for prices and averages and avoids noise like 9.879999999.
        return str(round(value, 4))
    return str(value)
