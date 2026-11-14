"""Checks a SQL string before it is run.

Why: the SQL is written by a language model, so we treat it like input from a
stranger. We do not search the text for words like "DROP" (easy to trick, and
it would block a harmless column called "drop_date"). We parse the SQL into a
tree with sqlglot and check the structure: what kind of statement it is and
which tables it reads.

Every message says what is wrong and what to do instead, because the model
reads the message and writes a better query.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

# Statements that change data. A SELECT can contain these inside a WITH clause
# in some SQL dialects, so we also look for them inside the tree.
_WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop)


def check_sql(sql: str, allowed_tables: list[str]) -> str | None:
    """Return None when the SQL is allowed, otherwise a message explaining why not."""
    allowed = {name.lower() for name in allowed_tables}
    allowed_text = ", ".join(sorted(allowed))

    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except SqlglotError as error:
        first_line = str(error).splitlines()[0]
        return f"The SQL could not be parsed: {first_line} Fix the syntax and try again."

    if len(statements) != 1:
        return (
            f"Expected exactly one SQL statement, but found {len(statements)}. "
            "Send a single SELECT statement."
        )

    statement = statements[0]
    if not isinstance(statement, exp.Query):
        kind = type(statement).__name__.upper()
        return (
            f"Only SELECT statements are allowed, but this is a {kind} statement. "
            "The database is read-only. Write a SELECT query instead."
        )

    if statement.find(*_WRITE_NODES) is not None:
        return (
            "The query contains a statement that changes data. "
            "The database is read-only. Use only SELECT."
        )

    # Names defined by WITH name AS (...) are not real tables, so they are fine.
    cte_names = {cte.alias.lower() for cte in statement.find_all(exp.CTE)}

    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            # A function in the FROM clause, for example read_csv('file').
            # range() / generate_series() only produce numbers, so they are safe.
            if isinstance(table.this, exp.GenerateSeries):
                continue
            return (
                f"The table function {table.this.sql(dialect='duckdb')[:60]} is not allowed. "
                f"Query only these tables: {allowed_text}."
            )

        name = table.name.lower()
        schema = table.db.lower()
        if not schema and name in cte_names:
            continue
        full_name = f"{schema}.{name}" if schema else name
        if full_name in allowed:
            continue

        # Most common mistake: the right table without its schema.
        match = [a for a in allowed if a.split(".")[1] == name] if not schema else []
        if match:
            return f"Table {name} must be written with its schema: use {match[0]}."
        return f"Table {full_name} is not available. Query only these tables: {allowed_text}."

    return None
