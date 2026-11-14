"""Describes the tools to the model and runs the tool the model asks for.

Why two parts: the model cannot see our Python functions. It only sees a
description of each tool: a name, a sentence about when to use it, and the
inputs as a JSON schema (the standard way to describe "this tool takes a text
called sql"). When the model asks for a tool, execute_tool() finds the matching
function and runs it.

The schemas are written by hand, not generated from the functions. It is a few
more lines, but you can read exactly what the model is told.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import duckdb

from agent import tools


@dataclass
class ToolSpec:
    name: str
    description: str            # written for the model: what it does, when to use it
    parameters: dict[str, Any]  # JSON schema of the inputs
    function: Callable[..., str]


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="list_tables",
        description=(
            "List all tables you can query, each with a description and its row count. "
            "Call this first when you do not know which tables exist."
        ),
        parameters=_schema({}, []),
        function=tools.list_tables,
    ),
    ToolSpec(
        name="describe_table",
        description=(
            "Show the columns of one table: name, type and what each column means. "
            "Call this before you write SQL for a table, so you use the right column "
            "names and understand the values."
        ),
        parameters=_schema(
            {"table": {"type": "string", "description": "Table name with schema, for example marts.dim_date."}},
            ["table"],
        ),
        function=tools.describe_table,
    ),
    ToolSpec(
        name="sample_rows",
        description=(
            "Show up to 5 example rows of one table. Use it to see what the values look "
            "like (for example how an item_id is written). Not for answering questions: "
            "the rows are only examples."
        ),
        parameters=_schema(
            {
                "table": {"type": "string", "description": "Table name with schema, for example marts.dim_item."},
                "n": {"type": "integer", "description": "Number of rows, 1 to 5. Default 5."},
            },
            ["table"],
        ),
        function=tools.sample_rows,
    ),
    ToolSpec(
        name="run_sql",
        description=(
            "Run one SELECT query (DuckDB SQL) and get the result as a text table. "
            "Only a single SELECT on the listed tables is allowed, and table names need "
            "their schema (marts.fct_daily_sales). The result is cut after a maximum "
            "number of rows, so aggregate in SQL instead of reading many rows. If the "
            "result starts with ERROR, read the message, fix the query and try again."
        ),
        parameters=_schema(
            {"sql": {"type": "string", "description": "One SELECT statement."}},
            ["sql"],
        ),
        function=tools.run_sql,
    ),
]

_BY_NAME = {spec.name: spec for spec in TOOLS}


def tool_schemas() -> list[dict[str, Any]]:
    """The tool list in neutral form. Each model backend converts it to its API's format."""
    return [
        {"name": spec.name, "description": spec.description, "parameters": spec.parameters}
        for spec in TOOLS
    ]


def execute_tool(con: duckdb.DuckDBPyConnection, name: str, arguments: dict[str, Any]) -> str:
    """Run the tool the model asked for and always return text.

    Why it never raises: the result goes back to the model. A message starting
    with "ERROR:" lets the model correct itself; an exception would end the run.
    """
    spec = _BY_NAME.get(name)
    if spec is None:
        return f"ERROR: Unknown tool {name!r}. Available tools: {', '.join(_BY_NAME)}."

    # Only pass inputs that are in the schema. Without this check the model
    # could set run_sql's max_rows or timeout_seconds and switch off a guardrail.
    allowed = spec.parameters["properties"].keys()
    unexpected = [key for key in arguments if key not in allowed]
    if unexpected:
        return f"ERROR: Tool {name} does not accept the input {unexpected}. Allowed inputs: {list(allowed)}."
    missing = [key for key in spec.parameters["required"] if key not in arguments]
    if missing:
        return f"ERROR: Tool {name} needs the input {missing}."

    try:
        return spec.function(con, **arguments)
    except Exception as error:  # noqa: BLE001 - any failure becomes text for the model
        return f"ERROR: Tool {name} failed: {error}"
