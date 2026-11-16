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
from typing import Any

import duckdb

from agent.config import load_config, project_path
from agent.llm import Model, ToolResult
from agent.schema_doc import load_general_notes
from agent.toolbox import execute_tool, tool_schemas
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
    final_sql: str | None = None  # the last SQL that ran without error: the agent's final result
    trace_path: Path | None = None


def build_system_prompt() -> str:
    """The prompt file plus the general notes about the data from docs/schema.md."""
    prompt_file = project_path(load_config()["agent"]["system_prompt"])
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    return f"{prompt}\n\n## About the data\n\n{load_general_notes()}"


def run_agent(
    question: str,
    model: Model,
    con: duckdb.DuckDBPyConnection,
    tracer: Tracer | None = None,
    max_steps: int | None = None,
) -> AgentResult:
    """Answer one question: run the loop until the model gives a final answer."""
    config = load_config()["agent"]
    max_steps = max_steps if max_steps is not None else config["max_steps"]
    tracer = tracer or Tracer()  # without a path it only keeps the events in memory

    system = build_system_prompt()
    tools = tool_schemas()
    messages: list[dict[str, Any]] = [{"role": "user", "text": question}]
    result = AgentResult(answer="", stopped="max_steps", steps=0, trace_path=tracer.path)

    tracer.log(
        "question",
        question=question,
        model=model.name,
        system_prompt=config["system_prompt"],
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
            cost_usd=reply.cost_usd,
            cached=reply.cached,
            seconds=model_seconds,
        )
        messages.append({"role": "assistant", "reply": reply})

        # 2. No tool request means the model is done: its text is the answer.
        if not reply.tool_calls:
            result.answer = reply.text
            result.stopped = "answer"
            break

        # 3. Otherwise run every tool it asked for and hand all results back together.
        results: list[ToolResult] = []
        for call in reply.tool_calls:
            started = time.monotonic()
            content = execute_tool(con, call.name, call.arguments)
            if call.name == "run_sql" and "sql" in call.arguments:
                result.sql_queries.append(str(call.arguments["sql"]))
                if not content.startswith("ERROR"):
                    result.final_sql = str(call.arguments["sql"])
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
        messages.append({"role": "tool_results", "results": results})

    if result.stopped == "max_steps":
        # The model was still asking for tools at the limit. We say so plainly
        # and do not pass off an unfinished run as an answer.
        result.answer = f"I could not finish this question within {max_steps} steps."

    tracer.log(
        "final",
        answer=result.answer,
        stopped=result.stopped,
        steps=result.steps,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        final_sql=result.final_sql,
    )
    return result
