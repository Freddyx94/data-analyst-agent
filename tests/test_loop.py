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
    for variant in ("v1", "v2"):
        prompt = build_system_prompt(variant)
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


# --- variant v2: the agent hands in its result with submit_answer ---------------

BREAKDOWN = "SELECT dept_id, count(*) AS items FROM marts.dim_item GROUP BY dept_id"
NARROW = "SELECT count(*) AS items FROM marts.dim_item WHERE dept_id = 'FOODS_3'"


def _submit(answer: str, sql: str | None = None) -> ModelReply:
    arguments = {"answer": answer} if sql is None else {"answer": answer, "sql": sql}
    return ModelReply(tool_calls=[ToolCall(id="submit", name="submit_answer", arguments=arguments)])


def test_v1_sends_only_the_four_database_tools(con):
    model = FakeModel([ModelReply(text="done")])
    run_agent("q", model, con, variant="v1")
    assert [t["name"] for t in model.calls[0]["tools"]] == ["list_tables", "describe_table", "sample_rows", "run_sql"]


def test_v2_also_offers_submit_answer_and_uses_prompt_v2(con):
    model = FakeModel([ModelReply(text="done")])
    run_agent("q", model, con, variant="v2")
    assert [t["name"] for t in model.calls[0]["tools"]][-1] == "submit_answer"
    assert "submit_answer" in model.calls[0]["system"]
    assert "submit_answer" not in build_system_prompt("v1")


def test_submitted_query_is_the_final_result_not_the_last_query(con):
    """The agent runs the narrow query, then a breakdown, and hands in the narrow one."""
    model = FakeModel([_call("run_sql", sql=NARROW), _call("run_sql", sql=BREAKDOWN), _submit("2 items.", NARROW)])
    result = run_agent("How many items are in FOODS_3?", model, con, variant="v2")
    assert result.stopped == "answer" and result.submitted
    assert result.answer == "2 items."
    assert result.final_sql == NARROW
    assert result.steps == 3
    assert len(model.calls) == 3  # the model is not called again after an accepted submission


def test_submitting_a_query_that_was_never_run_is_rejected(con):
    model = FakeModel([_submit("2 items.", NARROW), _call("run_sql", sql=NARROW), _submit("2 items.", NARROW)])
    result = run_agent("How many items are in FOODS_3?", model, con, variant="v2")
    rejection = model.calls[1]["messages"][-1]["results"][0].content
    assert rejection.startswith("ERROR") and "run_sql" in rejection
    assert result.submitted and result.final_sql == NARROW and result.steps == 3


def test_submitting_a_query_that_failed_is_rejected(con):
    bad = "SELECT nope FROM marts.dim_item"
    model = FakeModel([_call("run_sql", sql=bad), _submit("…", bad), ModelReply(text="I give up.")])
    result = run_agent("q", model, con, variant="v2")
    assert not result.submitted
    assert model.calls[2]["messages"][-1]["results"][0].content.startswith("ERROR")


def test_spaces_and_a_final_semicolon_do_not_matter_when_submitting(con):
    model = FakeModel([_call("run_sql", sql=NARROW), _submit("2 items.", "  " + NARROW.replace(" FROM", "\n FROM") + " ; ")])
    assert run_agent("q", model, con, variant="v2").submitted


def test_a_decline_is_submitted_without_a_query(con):
    model = FakeModel([_call("list_tables"), _submit("I can't answer that from this data. There are no returns.")])
    result = run_agent("How many returns?", model, con, variant="v2")
    assert result.submitted and result.final_sql is None
    assert result.answer.startswith("I can't answer")


def test_submission_without_answer_text_is_rejected(con):
    model = FakeModel([_call("run_sql", sql=NARROW), _submit("", NARROW), ModelReply(text="2 items.")])
    result = run_agent("q", model, con, variant="v2")
    assert not result.submitted
    assert result.final_sql == NARROW  # fallback: the last query that worked


def test_text_only_answer_still_ends_the_run_with_the_old_fallback(con):
    model = FakeModel([_call("run_sql", sql=NARROW), _call("run_sql", sql=BREAKDOWN), ModelReply(text="2 items.")])
    result = run_agent("q", model, con, variant="v2")
    assert result.stopped == "answer" and not result.submitted
    assert result.final_sql == BREAKDOWN


def test_in_v1_submit_answer_is_just_an_unknown_tool(con):
    model = FakeModel([_submit("2 items."), ModelReply(text="2 items.")])
    result = run_agent("q", model, con, variant="v1")
    assert not result.submitted
    assert model.calls[1]["messages"][-1]["results"][0].content.startswith("ERROR: Unknown tool")


# --- variant v3: the hand-in check ------------------------------------------------


def test_row_count_is_read_from_the_run_sql_text():
    from agent.answers import row_count

    assert row_count("n\n4\n(1 rows)") == 1
    assert row_count("a | b\n1 | 2\n3 | 4\n(2 rows)") == 2
    assert row_count("x\n1\n(showing the first 200 rows; the result has more. Aggregate ...)") == 201
    assert row_count("ERROR: The database could not run the query.") is None
    assert row_count("") is None


def test_v3_sends_a_multi_row_query_back_once_and_accepts_a_narrow_one(con):
    model = FakeModel([
        _call("run_sql", sql=BREAKDOWN),
        _submit("FOODS_3 has 2 items.", BREAKDOWN),  # 3 rows: sent back with a question
        _call("run_sql", sql=NARROW),
        _submit("FOODS_3 has 2 items.", NARROW),     # 1 row: accepted
    ])
    result = run_agent("How many items are in FOODS_3?", model, con, variant="v3")
    check = model.calls[2]["messages"][-1]["results"][0].content
    assert check.startswith("CHECK:") and "3 rows" in check
    assert result.submitted and result.final_sql == NARROW and result.steps == 4


def test_v3_accepts_the_same_multi_row_query_the_second_time(con):
    """For a question that really asks for a list, the agent confirms by submitting again."""
    model = FakeModel([
        _call("run_sql", sql=BREAKDOWN),
        _submit("Items per department: ...", BREAKDOWN),
        _submit("Items per department: ...", BREAKDOWN),
    ])
    result = run_agent("How many items does each department have?", model, con, variant="v3")
    assert result.submitted and result.final_sql == BREAKDOWN and result.steps == 3


def test_v3_requires_a_query_unless_the_agent_declines(con):
    model = FakeModel([
        _call("run_sql", sql=NARROW),
        _submit("FOODS_3 has 2 items."),           # no query and not a decline: rejected
        _submit("FOODS_3 has 2 items.", NARROW),
    ])
    result = run_agent("How many items are in FOODS_3?", model, con, variant="v3")
    assert model.calls[2]["messages"][-1]["results"][0].content.startswith("ERROR")
    assert result.final_sql == NARROW

    decline = FakeModel([_submit("I can't answer that from this data. There are no returns.")])
    assert run_agent("How many returns?", decline, con, variant="v3").submitted


def test_v2_has_no_hand_in_check(con):
    """v2 must behave exactly as measured: a multi-row query is accepted at once."""
    model = FakeModel([_call("run_sql", sql=BREAKDOWN), _submit("2 items.", BREAKDOWN)])
    result = run_agent("q", model, con, variant="v2")
    assert result.submitted and result.steps == 2


# --- variant v4: `sql` required in the schema, queries compared by parsed form ------


def test_v4_tool_schema_requires_sql_and_v3_does_not(con):
    for variant, required in (("v3", ["answer"]), ("v4", ["answer", "sql"])):
        model = FakeModel([ModelReply(text="done")])
        run_agent("q", model, con, variant=variant)
        submit = model.calls[0]["tools"][-1]
        assert submit["name"] == "submit_answer"
        assert submit["parameters"]["required"] == required


def test_v4_matches_a_query_that_is_only_formatted_differently(con):
    """Like the real failure: run as '( SELECT', handed in as '(SELECT'."""
    ran = "WITH x AS ( SELECT count(*) AS n FROM marts.dim_item ) SELECT n FROM x"
    handed_in = "with x as (select count(*) as n\n  from marts.dim_item)\nselect n from x;"
    for variant, accepted in (("v3", False), ("v4", True)):
        model = FakeModel([_call("run_sql", sql=ran), _submit("4 items.", handed_in), ModelReply(text="4 items.")])
        assert run_agent("How many items?", model, con, variant=variant).submitted is accepted


def test_v4_accepts_a_decline_with_an_empty_query(con):
    model = FakeModel([_submit("I can't answer that from this data. There are no returns.", "")])
    result = run_agent("How many returns?", model, con, variant="v4")
    assert result.submitted and result.final_sql is None


def test_v4_uses_prompt_v3():
    assert "`sql` set to an empty string" in build_system_prompt("v4")
    assert "`sql` set to an empty string" not in build_system_prompt("v3")
