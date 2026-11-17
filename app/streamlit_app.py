"""Chat app for the data analyst agent.

    streamlit run app/streamlit_app.py

Streamlit runs this whole script again after every interaction. Everything that
must survive between runs (connection, model, chat history, cost) is kept in
st.session_state, which exists once per browser tab.

Two environment variables exist for automatic tests, so they need neither the
real data nor an API key:
    AGENT_DATABASE     path of the database to open (default: config.yaml)
    AGENT_FAKE_SCRIPT  path of scripted model replies (tests/fixtures/scripts/*.json)
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st

from agent.chart import choose_chart
from agent.config import agent_settings
from agent.db import connect
from agent.llm import FakeModel, ModelError, make_model
from agent.loop import run_agent
from agent.show_trace import format_trace
from agent.tools import SqlError, query_rows
from agent.trace import Tracer, new_trace_path

TABLE_MAX_ROWS = 1000  # the table shows at most this many rows of the final result

EXAMPLES = [
    "Which department had the highest revenue in 2015?",
    "How many units were sold in each month of 2014?",
    "Which forecast model was the most accurate?",
]


# --- setup, once per browser tab ----------------------------------------------------------


def setup() -> None:
    """Open the database and create the model the first time the page runs."""
    if "con" not in st.session_state:
        st.session_state.con = connect(os.environ.get("AGENT_DATABASE") or None)
    if "model" not in st.session_state:
        script = os.environ.get("AGENT_FAKE_SCRIPT")
        try:
            st.session_state.model = FakeModel.from_file(script) if script else make_model()
        except ModelError as error:
            st.error(f"The app cannot start: {error}")
            st.stop()
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("total_cost", 0.0)


# --- one question ---------------------------------------------------------------------------


def ask(question: str) -> dict[str, Any]:
    """Run the agent on one question and collect everything the page shows about it."""
    # Test mode keeps the trace in memory, so tests do not write files.
    tracer = Tracer() if os.environ.get("AGENT_FAKE_SCRIPT") else Tracer(new_trace_path())
    entry: dict[str, Any] = {"question": question, "error": None, "columns": [], "rows": [], "events": tracer.events}
    try:
        result = run_agent(question, st.session_state.model, st.session_state.con, tracer)
    except ModelError as error:
        entry["error"] = f"The model call failed: {error}"
        return entry

    entry["result"] = result
    st.session_state.total_cost += result.cost_usd
    if result.final_sql:
        # The table and chart come from the final query, run again through the same guardrails.
        try:
            columns, rows, _ = query_rows(st.session_state.con, result.final_sql, max_rows=TABLE_MAX_ROWS)
            entry["columns"], entry["rows"] = columns, rows
        except SqlError as error:
            entry["error"] = f"The final query could not be shown: {error}"
    return entry


# --- drawing ----------------------------------------------------------------------------------


def as_markdown(text: str) -> str:
    """Streamlit reads $...$ as a math formula; "$251,336 and $244,394" must stay text."""
    return text.replace("$", "\\$")


def unique_columns(columns: list[str]) -> list[str]:
    """The table needs distinct column names; a query can return two columns with the same name."""
    seen: dict[str, int] = {}
    names = []
    for name in columns:
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return names


def show_entry(entry: dict[str, Any]) -> None:
    with st.chat_message("user"):
        st.markdown(as_markdown(entry["question"]))
    with st.chat_message("assistant"):
        result = entry.get("result")
        if result is not None:
            st.markdown(as_markdown(result.answer))
            if result.ungrounded_numbers:
                st.warning(
                    "These numbers do not come from a query result in this run: "
                    f"{', '.join(result.ungrounded_numbers)}. Treat them with care."
                )
        if entry["error"]:
            st.error(entry["error"])

        if entry["rows"]:
            table = pd.DataFrame(entry["rows"], columns=unique_columns(entry["columns"]))
            spec = choose_chart(list(table.columns), entry["rows"])
            if spec is not None:
                draw = st.line_chart if spec.kind == "line" else st.bar_chart
                draw(table, x=spec.x, y=spec.y)
            st.dataframe(table, hide_index=True)
        if result is not None and result.final_sql:
            with st.expander("SQL used"):
                st.code(result.final_sql, language="sql")
        with st.expander("Show steps"):
            st.code(format_trace(entry["events"]) or "(no steps)", language=None)


def show_sidebar() -> None:
    settings = agent_settings()
    with st.sidebar:
        st.header("Data analyst agent")
        st.caption(f"Model: {st.session_state.model.name} · agent {settings['name']}")
        st.caption("Walmart M5 data: store CA_1, FOODS, 2011-01-29 to 2016-05-22. Read-only.")
        last = next((e.get("result") for e in reversed(st.session_state.history) if e.get("result")), None)
        if last is not None:
            st.subheader("Last question")
            st.metric("Steps", last.steps)
            st.metric("Cost", f"${last.cost_usd:.4f}")
            st.caption(f"{last.input_tokens:,} tokens in, {last.output_tokens:,} tokens out, {last.seconds:.1f} s")
        st.subheader("This session")
        st.metric("Total cost", f"${st.session_state.total_cost:.4f}")
        st.caption("Each question is answered on its own: the agent does not remember earlier questions.")


def main() -> None:
    st.set_page_config(page_title="Data Analyst Agent", page_icon=":bar_chart:", layout="wide")
    setup()
    show_sidebar()
    st.title("Ask the sales data")
    if not st.session_state.history:
        st.caption("For example: " + " · ".join(EXAMPLES))

    for entry in st.session_state.history:
        show_entry(entry)

    question = st.chat_input("Ask a question about the sales data")
    if question:
        with st.spinner("The agent is working ..."):
            entry = ask(question)
        st.session_state.history.append(entry)
        st.rerun()  # draw the page again, now with the new answer and the updated sidebar


main()
