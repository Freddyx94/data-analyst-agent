"""The setups the evaluation compares. Each one answers a question and returns an Outcome.

- baseline: one prompt with the whole schema, the model writes one SQL query.
            No tools, no look at the data, no second try.
- agent:    the loop from src/agent/loop.py with the four tools.

Both return the same Outcome, so the scoring code does not care which setup ran.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb

from agent.config import PROJECT_ROOT, load_config
from agent.llm import Model
from agent.loop import run_agent
from agent.schema_doc import load_general_notes
from agent.tools import describe_table
from agent.trace import Tracer

DECLINE_WORD = "CANNOT_ANSWER"
DECLINE_ANSWER = "I can't answer that from this data."
BASELINE_PROMPT = "prompts/baseline_v1.md"


@dataclass
class Outcome:
    """What one setup produced for one question."""

    answer: str              # the text answer (the baseline has none, except a decline)
    final_sql: str | None    # the query whose result is the answer
    stopped: str             # "answer" or "max_steps"
    steps: int               # number of model calls
    input_tokens: int
    output_tokens: int
    cost_usd: float          # cost at list price
    spent_usd: float         # money really spent now (0 for cached replies)
    seconds: float


def build_baseline_prompt(con: duckdb.DuckDBPyConnection) -> str:
    """The baseline prompt with every table description filled in.

    The agent finds this information step by step with its tools. The baseline
    gets all of it up front, because it has no tools to ask.
    """
    tables = load_config()["sql"]["allowed_tables"]
    schema = "\n\n".join(describe_table(con, table) for table in tables)
    template = (PROJECT_ROOT / BASELINE_PROMPT).read_text(encoding="utf-8")
    return template.replace("{general_notes}", load_general_notes()).replace("{schema}", schema)


def extract_sql(text: str) -> str | None:
    """Take the query out of the model's reply.

    The prompt asks for a ```sql block. If the model forgot the block but the
    reply starts like a query, we still accept it: the baseline should fail
    because of wrong SQL, not because of formatting.
    """
    blocks = re.findall(r"```(?:sql)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if blocks:
        return blocks[-1].strip() or None
    stripped = text.strip()
    return stripped if stripped.lower().startswith(("select", "with")) else None


def run_baseline(question: str, model: Model, con: duckdb.DuckDBPyConnection) -> Outcome:
    """One model call, no tools. The reply is a query or the decline word."""
    reply = model.reply(build_baseline_prompt(con), [{"role": "user", "text": question}], tools=[])
    declined = DECLINE_WORD in reply.text
    return Outcome(
        answer=DECLINE_ANSWER if declined else "",
        final_sql=None if declined else extract_sql(reply.text),
        stopped="answer",
        steps=1,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        cost_usd=reply.cost_usd,
        spent_usd=0.0 if reply.cached else reply.cost_usd,
        seconds=reply.seconds,
    )


def run_agent_setup(
    question: str, model: Model, con: duckdb.DuckDBPyConnection, tracer: Tracer | None = None,
    variant: str | None = None,
) -> Outcome:
    """The agent loop (in the given variant), reported in the same shape as the baseline."""
    result = run_agent(question, model, con, tracer, variant=variant)
    return Outcome(
        answer=result.answer,
        final_sql=result.final_sql,
        stopped=result.stopped,
        steps=result.steps,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        spent_usd=result.spent_usd,
        seconds=result.seconds,
    )
