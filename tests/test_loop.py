"""Tests for the agent loop, using the fake model: no key, no cost, same result every time."""

from __future__ import annotations

import inspect
from pathlib import Path

from agent.llm import FakeModel, ModelReply, ToolCall
from agent.loop import build_system_prompt, run_agent
from agent.show_trace import format_trace
from agent.toolbox import TOOLS, execute_tool, tool_schemas
from agent.trace import Tracer, read_trace

SCRIPT = Path(__file__).parent / "fixtures" / "scripts" / "units_by_department.json"


def _call(name: str, **arguments) -> ModelReply:
    """A scripted model reply that asks for one tool."""
    return ModelReply(tool_calls=[ToolCall(id=f"id_{name}", name=name, arguments=arguments)])


def test_scripted_run_ends_with_the_final_answer(con):
    result = run_agent("How many units did each department sell?", FakeModel.from_file(SCRIPT), con)
    assert result.stopped == "answer"
    assert result.steps == 5
    assert "29 units" in result.answer
    assert len(result.sql_queries) == 2  # the wrong query and the corrected one


def test_text_only_reply_stops_the_loop_at_once(con):
    model = FakeModel([ModelReply(text="I can't answer that from this data.")])
    result = run_agent("What is the profit?", model, con)
    assert result.stopped == "answer"
    assert result.steps == 1
    assert len(model.calls) == 1


def test_tool_results_are_sent_back_to_the_model(con):
    model = FakeModel([_call("run_sql", sql="SELECT count(*) AS n FROM marts.dim_item"), ModelReply(text="4 items.")])
    run_agent("How many items?", model, con)

    second_call = model.calls[1]["messages"]
    assert [m["role"] for m in second_call] == ["user", "assistant", "tool_results"]
    tool_result = second_call[-1]["results"][0]
    assert tool_result.call_id == "id_run_sql"
    assert tool_result.content.splitlines()[1] == "4"


def test_several_tool_calls_in_one_step_return_all_results_together(con):
    two_calls = ModelReply(
        tool_calls=[
            ToolCall(id="a", name="describe_table", arguments={"table": "marts.dim_item"}),
            ToolCall(id="b", name="describe_table", arguments={"table": "marts.dim_store"}),
        ]
    )
    model = FakeModel([two_calls, ModelReply(text="done")])
    run_agent("Describe two tables", model, con)
    results = model.calls[1]["messages"][-1]["results"]
    assert [r.call_id for r in results] == ["a", "b"]


def test_an_error_goes_back_to_the_model_and_the_loop_continues(con):
    model = FakeModel([_call("run_sql", sql="DROP TABLE marts.dim_item"), ModelReply(text="I cannot do that.")])
    result = run_agent("Drop the item table", model, con)
    assert model.calls[1]["messages"][-1]["results"][0].content.startswith("ERROR")
    assert result.stopped == "answer"
    assert con.execute("SELECT count(*) FROM marts.dim_item").fetchone()[0] == 4


def test_loop_stops_at_max_steps(con):
    endless = [_call("list_tables") for _ in range(10)]
    model = FakeModel(endless)
    result = run_agent("Loop forever", model, con, max_steps=3)
    assert result.stopped == "max_steps"
    assert result.steps == 3
    assert len(model.calls) == 3  # the model was not called a fourth time
    assert "3 steps" in result.answer


def test_unknown_tool_returns_an_error_text(con):
    assert execute_tool(con, "delete_everything", {}).startswith("ERROR: Unknown tool")


def test_model_cannot_switch_off_a_guardrail_through_extra_inputs(con):
    """run_sql has max_rows and timeout_seconds in Python, but the model must not set them."""
    result = execute_tool(con, "run_sql", {"sql": "SELECT * FROM range(1000)", "max_rows": 100000})
    assert result.startswith("ERROR")
    assert "max_rows" in result


def test_missing_input_returns_an_error_text(con):
    assert execute_tool(con, "describe_table", {}).startswith("ERROR")


def test_tokens_are_added_up_over_all_steps(con):
    model = FakeModel(
        [
            ModelReply(tool_calls=[ToolCall("x", "list_tables", {})], input_tokens=100, output_tokens=20),
            ModelReply(text="done", input_tokens=150, output_tokens=30),
        ]
    )
    result = run_agent("q", model, con)
    assert (result.input_tokens, result.output_tokens) == (250, 50)


def test_trace_file_records_every_step(con, tmp_path):
    tracer = Tracer(tmp_path / "trace.jsonl")
    run_agent("How many units did each department sell?", FakeModel.from_file(SCRIPT), con, tracer)

    events = read_trace(tmp_path / "trace.jsonl")
    kinds = [e["event"] for e in events]
    assert kinds[0] == "question" and kinds[-1] == "final"
    assert kinds.count("model_reply") == 5
    assert kinds.count("tool_result") == 5  # 1 + 2 + 1 + 1
    failed = [e for e in events if e["event"] == "tool_result" and e["result"].startswith("ERROR")]
    assert len(failed) == 1

    text = format_trace(events)
    assert "QUESTION: How many units did each department sell?" in text
    assert "asks for tool: run_sql" in text
    assert "ANSWER: FOODS_3 sold the most" in text


def test_system_prompt_contains_the_rules_and_the_notes_about_the_data():
    prompt = build_system_prompt()
    assert "I can't answer that from this data" in prompt
    assert "2016-05-22" in prompt  # from the general notes in docs/schema.md


def test_tool_descriptions_match_the_python_functions():
    """What we tell the model about a tool's inputs must fit the real function."""
    assert [t["name"] for t in tool_schemas()] == ["list_tables", "describe_table", "sample_rows", "run_sql"]
    for spec in TOOLS:
        properties = set(spec.parameters["properties"])
        assert set(spec.parameters["required"]) <= properties
        function_inputs = set(inspect.signature(spec.function).parameters) - {"con"}
        assert properties <= function_inputs, f"{spec.name}: schema has inputs the function lacks"
