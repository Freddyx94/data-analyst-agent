"""The agent loop, written by hand.

The whole idea in five lines:

    repeat:
        send the conversation to the model
        if the model asks for tools: run them, add the results to the conversation
        if the model answers with text only: that is the final answer, stop
        stop anyway after max_steps model calls

The model has no memory between calls. "The conversation" is a list that we
keep and send again in full on every call; that list is the agent's memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import duckdb
import sqlglot
from sqlglot.errors import SqlglotError

from agent.answers import is_decline, row_count
from agent.config import agent_settings, project_path
from agent.llm import Model, ToolResult
from agent.schema_doc import load_general_notes
from agent.toolbox import SUBMIT_TOOL, execute_tool, tool_schemas
from agent.trace import Tracer


@dataclass
class AgentResult:
    """Everything we want to know about one run, for the chat, the app and the evaluation."""

    answer: str
    stopped: str                 # "answer" = the model finished, "max_steps" = we cut it off
    steps: int                   # number of model calls
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0    # what the run costs at list price
    spent_usd: float = 0.0   # what was really paid now (cached replies cost nothing)
    seconds: float = 0.0     # model time plus tool time
    sql_queries: list[str] = field(default_factory=list)  # every SQL the agent ran
    final_sql: str | None = None  # the query whose result is the agent's final result
    submitted: bool = False       # True when the agent handed in its result with submit_answer
    trace_path: Path | None = None


def build_system_prompt(variant: str | None = None) -> str:
    """The prompt file plus the general notes about the data from docs/schema.md."""
    prompt_file = project_path(agent_settings(variant)["system_prompt"])
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    return f"{prompt}\n\n## About the data\n\n{load_general_notes()}"


def _same_sql(sql: str) -> str:
    """Ignore differences in spaces, line breaks and a final semicolon when comparing queries."""
    return " ".join(sql.split()).rstrip(";").strip()


def _parsed_sql(sql: str) -> str:
    """Write a query in sqlglot's standard form, so formatting differences disappear.

    "( SELECT" and "(SELECT", line breaks or "select" and "SELECT" give the same
    text. If the query cannot be parsed, fall back to the plain comparison.
    """
    try:
        return sqlglot.parse_one(sql, read="duckdb").sql(dialect="duckdb")
    except (SqlglotError, ValueError):
        return _same_sql(sql)


def check_submission(
    arguments: dict[str, Any],
    successful_sql: dict[str, int | None],
    strict: bool = False,
    asked_to_confirm: set[str] | None = None,
    key_of: Callable[[str], str] = _same_sql,
) -> tuple[bool, str]:
    """Decide if a submit_answer call is accepted. Return (accepted?, message for the model).

    Always: the submitted query must be one that already ran without error in
    this run. Why: then the agent has seen the result it is handing in, and the
    table the user sees cannot contradict the text.

    With strict=True (variant v3), two more checks:
    - An answer that is not a decline must come with its query.
    - A query that returned more than one row is sent back once with a question.
      The loop cannot know how many rows the question needs, so it asks; if the
      agent submits the same query again, it is accepted.
    `successful_sql` maps each query that ran (normalised with `key_of`) to its
    number of rows. `asked_to_confirm` remembers which queries were already sent back.
    """
    answer = str(arguments.get("answer") or "").strip()
    sql = str(arguments.get("sql") or "").strip()
    if not answer:
        return False, "ERROR: submit_answer needs the input `answer` with your answer text."
    if sql and key_of(sql) not in successful_sql:
        return False, (
            "ERROR: You have not run this query with run_sql in this conversation (or it failed). "
            "Run it with run_sql, check its result, then call submit_answer with exactly the same query."
        )
    if not strict:
        return True, "Answer accepted."

    if not sql:
        if is_decline(answer):
            return True, "Answer accepted."
        return False, (
            "ERROR: Your answer is not a decline, so submit_answer needs `sql`: the query whose "
            "result is the answer. Leave `sql` out only when you say \"I can't answer that from this data\"."
        )

    key = key_of(sql)
    rows = successful_sql[key]
    if rows is not None and rows > 1 and asked_to_confirm is not None and key not in asked_to_confirm:
        asked_to_confirm.add(key)
        return False, (
            f"CHECK: The query you submitted returns {rows} rows. If the question asks for one thing "
            "(one department, one number, one date), run a query that returns only that one row and "
            f"submit it. If the question really asks for a list or a breakdown of {rows} rows, call "
            "submit_answer again with the same query."
        )
    return True, "Answer accepted."


def run_agent(
    question: str,
    model: Model,
    con: duckdb.DuckDBPyConnection,
    tracer: Tracer | None = None,
    max_steps: int | None = None,
    variant: str | None = None,
) -> AgentResult:
    """Answer one question: run the loop until the model gives a final answer."""
    settings = agent_settings(variant)
    max_steps = max_steps if max_steps is not None else settings["max_steps"]
    use_submit = bool(settings["submit_answer_tool"])
    strict_submit = bool(settings.get("check_submission", False))
    key_of = _parsed_sql if settings.get("sql_match") == "parsed" else _same_sql
    asked_to_confirm: set[str] = set()  # queries already sent back once for having several rows
    tracer = tracer or Tracer()  # without a path it only keeps the events in memory

    system = build_system_prompt(variant)
    tools = tool_schemas(include_submit=use_submit, sql_required=bool(settings.get("submit_sql_required", False)))
    messages: list[dict[str, Any]] = [{"role": "user", "text": question}]
    result = AgentResult(answer="", stopped="max_steps", steps=0, trace_path=tracer.path)
    successful_sql: list[str] = []  # queries that ran without error, in order
    rows_of: dict[str, int | None] = {}  # each of those queries (normalised) -> its number of rows

    tracer.log(
        "question",
        question=question,
        model=model.name,
        variant=settings["name"],
        system_prompt=settings["system_prompt"],
        max_steps=max_steps,
    )

    for step in range(1, max_steps + 1):
        # 1. Ask the model what to do next, given everything so far.
        started = time.monotonic()
        reply = model.reply(system, messages, tools)
        result.steps = step
        result.input_tokens += reply.input_tokens
        result.output_tokens += reply.output_tokens
        result.cost_usd += reply.cost_usd
        result.spent_usd += 0.0 if reply.cached else reply.cost_usd
        # A cached reply returns at once; use the time the model took originally.
        model_seconds = reply.seconds or round(time.monotonic() - started, 3)
        result.seconds += model_seconds
        tracer.log(
            "model_reply",
            step=step,
            text=reply.text,
            tool_calls=[{"name": c.name, "arguments": c.arguments} for c in reply.tool_calls],
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cache_read_tokens=reply.cache_read_tokens,
            cache_write_tokens=reply.cache_write_tokens,
            cost_usd=reply.cost_usd,
            cached=reply.cached,
            seconds=model_seconds,
        )
        messages.append({"role": "assistant", "reply": reply})

        # 2. No tool request means the model is done: its text is the answer.
        #    It did not hand in a query, so we fall back to the last one that worked.
        if not reply.tool_calls:
            result.answer = reply.text
            result.final_sql = successful_sql[-1] if successful_sql else None
            result.stopped = "answer"
            break

        # 3. Otherwise run every tool it asked for and hand all results back together.
        results: list[ToolResult] = []
        for call in reply.tool_calls:
            started = time.monotonic()
            if use_submit and call.name == SUBMIT_TOOL:
                # The agent hands in its result. If we accept it, the run is over.
                accepted, content = check_submission(
                    call.arguments, rows_of, strict_submit, asked_to_confirm, key_of
                )
                if accepted:
                    result.answer = str(call.arguments["answer"]).strip()
                    result.final_sql = str(call.arguments.get("sql") or "").strip() or None
                    result.submitted = True
                    result.stopped = "answer"
            else:
                content = execute_tool(con, call.name, call.arguments)
                if call.name == "run_sql" and "sql" in call.arguments:
                    result.sql_queries.append(str(call.arguments["sql"]))
                    if not content.startswith("ERROR"):
                        successful_sql.append(str(call.arguments["sql"]))
                        rows_of[key_of(str(call.arguments["sql"]))] = row_count(content)
            tool_seconds = round(time.monotonic() - started, 3)
            result.seconds += tool_seconds
            tracer.log(
                "tool_result",
                step=step,
                name=call.name,
                arguments=call.arguments,
                result=content,
                seconds=tool_seconds,
            )
            results.append(ToolResult(call_id=call.id, name=call.name, content=content))
            if result.submitted:
                break
        if result.submitted:
            break
        messages.append({"role": "tool_results", "results": results})

    if result.stopped == "max_steps":
        # The model was still asking for tools at the limit. We say so plainly
        # and do not pass off an unfinished run as an answer.
        result.answer = f"I could not finish this question within {max_steps} steps."
        result.final_sql = successful_sql[-1] if successful_sql else None

    tracer.log(
        "final",
        answer=result.answer,
        stopped=result.stopped,
        steps=result.steps,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        final_sql=result.final_sql,
        submitted=result.submitted,
    )
    return result
