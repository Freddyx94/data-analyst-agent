"""Tests for the Anthropic backend that need no API key and no network.

They cover the translation in both directions: our conversation to the API's
format, and the API's answer to our ModelReply. The API answer is imitated
with small stand-in objects that have the same fields.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from agent.config import load_env
from agent.llm import (
    AnthropicModel,
    ModelError,
    ModelReply,
    ToolCall,
    ToolResult,
    cost_usd,
    parse_anthropic_response,
    to_anthropic_messages,
    to_anthropic_tools,
)
from agent.toolbox import tool_schemas

MODEL = "claude-haiku-5-5"


def _response(blocks, stop_reason="end_turn", input_tokens=1000, output_tokens=200):
    """A stand-in for the API's answer object."""
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _block(**fields):
    """A stand-in for one block of the API's answer, with the to_dict() the real blocks have."""
    return SimpleNamespace(**fields, to_dict=lambda: dict(fields))


def _text(text):
    return _block(type="text", text=text)


def _tool_use(call_id, name, arguments):
    return _block(type="tool_use", id=call_id, name=name, input=arguments)


def test_cost_is_tokens_times_price():
    # 1,000,000 input tokens at $0.10 plus 1,000,000 output tokens at $0.50.
    assert cost_usd(MODEL, 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert cost_usd(MODEL, 2_000, 400) == pytest.approx(0.0004)


def test_cost_for_a_model_without_a_price_is_an_error_not_zero():
    with pytest.raises(ModelError, match="No price"):
        cost_usd("some-unknown-model", 100, 100)


def test_tools_are_translated_to_the_api_format():
    tools = to_anthropic_tools(tool_schemas())
    assert [t["name"] for t in tools] == ["list_tables", "describe_table", "sample_rows", "run_sql"]
    assert all(set(t) == {"name", "description", "input_schema"} for t in tools)
    assert tools[3]["input_schema"]["required"] == ["sql"]


def test_conversation_is_translated_to_the_api_format():
    reply = ModelReply(text="Let me look.", tool_calls=[ToolCall("c1", "run_sql", {"sql": "SELECT 1"})])
    messages = [
        {"role": "user", "text": "How many items?"},
        {"role": "assistant", "reply": reply},
        {"role": "tool_results", "results": [ToolResult("c1", "run_sql", "ERROR: no")]},
    ]
    converted = to_anthropic_messages(messages)

    assert converted[0] == {"role": "user", "content": "How many items?"}
    assert converted[1]["role"] == "assistant"
    assert converted[1]["content"] == [
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": "c1", "name": "run_sql", "input": {"sql": "SELECT 1"}},
    ]
    # Tool results travel as a user message and are linked by the call id.
    assert converted[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ERROR: no", "is_error": True}],
    }


def test_the_models_own_reply_is_sent_back_unchanged():
    original_blocks = [SimpleNamespace(type="thinking", thinking=""), _text("ok")]
    reply = ModelReply(text="ok", provider_content=original_blocks)
    converted = to_anthropic_messages([{"role": "assistant", "reply": reply}])
    assert converted[0]["content"] is original_blocks


def test_answer_with_a_tool_request_is_parsed():
    response = _response(
        [_text("I will count."), _tool_use("toolu_1", "run_sql", {"sql": "SELECT 1"})],
        stop_reason="tool_use",
    )
    reply = parse_anthropic_response(response, MODEL)
    assert reply.text == "I will count."
    assert reply.tool_calls == [ToolCall("toolu_1", "run_sql", {"sql": "SELECT 1"})]
    assert (reply.input_tokens, reply.output_tokens) == (1000, 200)
    assert reply.cost_usd == pytest.approx(0.0002)


def test_final_answer_has_no_tool_requests():
    reply = parse_anthropic_response(_response([_text("There are 4 items.")]), MODEL)
    assert reply.text == "There are 4 items."
    assert reply.tool_calls == []


def test_a_refusal_becomes_a_plain_final_message():
    reply = parse_anthropic_response(_response([], stop_reason="refusal"), MODEL)
    assert reply.tool_calls == []
    assert "declined" in reply.text


def test_a_cut_off_reply_does_not_run_half_written_tool_requests():
    response = _response([_tool_use("t", "run_sql", {})], stop_reason="max_tokens")
    reply = parse_anthropic_response(response, MODEL)
    assert reply.tool_calls == []
    assert "cut off" in reply.text


def test_missing_api_key_gives_a_clear_message(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("agent.llm.load_env", lambda: None)  # ignore a real .env file
    with pytest.raises(ModelError, match="No API key"):
        AnthropicModel(MODEL)


def test_load_env_reads_values_and_does_not_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\nTEST_AGENT_NEW=from_file\nTEST_AGENT_SET='from_file'\nTEST_AGENT_EMPTY=\n")
    monkeypatch.delenv("TEST_AGENT_NEW", raising=False)
    monkeypatch.delenv("TEST_AGENT_EMPTY", raising=False)
    monkeypatch.setenv("TEST_AGENT_SET", "from_shell")

    load_env(env_file)

    assert os.environ["TEST_AGENT_NEW"] == "from_file"
    assert os.environ["TEST_AGENT_SET"] == "from_shell"  # the environment wins
    assert "TEST_AGENT_EMPTY" not in os.environ
    monkeypatch.delenv("TEST_AGENT_NEW")


def test_reply_content_is_kept_as_plain_data_for_the_cache_and_the_next_call():
    response = _response([_text("I will count."), _tool_use("toolu_1", "run_sql", {"sql": "SELECT 1"})])
    reply = parse_anthropic_response(response, MODEL)
    assert reply.provider_content == [
        {"type": "text", "text": "I will count."},
        {"type": "tool_use", "id": "toolu_1", "name": "run_sql", "input": {"sql": "SELECT 1"}},
    ]


# --- prompt caching ---------------------------------------------------------------


def test_cost_counts_each_kind_of_input_at_its_own_price():
    # Haiku: input $0.10, cache write $0.125, cache read $0.01, output $0.50 per million.
    cost = cost_usd(MODEL, 1_000_000, 1_000_000, cache_write_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert cost == pytest.approx(0.10 + 0.50 + 0.125 + 0.01)


def test_usage_with_cache_tokens_is_parsed_and_priced():
    response = _response([_text("ok")], input_tokens=100, output_tokens=200)
    response.usage.cache_creation_input_tokens = 1_000
    response.usage.cache_read_input_tokens = 10_000
    reply = parse_anthropic_response(response, MODEL)
    assert reply.input_tokens == 11_100  # all input, so runs with and without caching compare
    assert (reply.cache_read_tokens, reply.cache_write_tokens) == (10_000, 1_000)
    expected = (100 * 0.10 + 200 * 0.50 + 1_000 * 0.125 + 10_000 * 0.01) / 1_000_000
    assert reply.cost_usd == pytest.approx(expected)


def test_usage_without_cache_fields_still_works():
    reply = parse_anthropic_response(_response([_text("ok")]), MODEL)  # stand-in has no cache fields
    assert (reply.cache_read_tokens, reply.cache_write_tokens) == (0, 0)


def test_cache_markers_for_the_agent_and_for_the_baseline():
    from agent.llm import add_cache_markers

    agent_request = {"system": "You are ...", "messages": [], "tools": [{"name": "run_sql"}]}
    add_cache_markers(agent_request)
    assert agent_request["system"] == [
        {"type": "text", "text": "You are ...", "cache_control": {"type": "ephemeral"}}
    ]
    assert agent_request["cache_control"] == {"type": "ephemeral"}

    baseline_request = {"system": "You are ...", "messages": []}
    add_cache_markers(baseline_request)
    assert baseline_request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in baseline_request  # the question at the end is never re-sent


def test_the_model_sends_cache_markers_when_caching_is_on(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    model = AnthropicModel(MODEL)
    sent = {}

    def fake_create(**request):
        sent.update(request)
        return _response([_text("ok")])

    monkeypatch.setattr(model._client.messages, "create", fake_create)
    model.reply("system text", [{"role": "user", "text": "q"}], tool_schemas())
    assert sent["system"][0]["text"] == "system text"
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["cache_control"] == {"type": "ephemeral"}


def test_cache_keeps_the_cache_token_counts(tmp_path):
    from agent.llm import CachedModel

    class OneReply:
        name = MODEL

        def reply(self, system, messages, tools):
            return ModelReply(text="ok", input_tokens=500, cache_read_tokens=400, cache_write_tokens=50)

    cached = CachedModel(OneReply(), tmp_path)
    cached.reply("s", [{"role": "user", "text": "q"}], [])
    again = cached.reply("s", [{"role": "user", "text": "q"}], [])
    assert again.cached and (again.cache_read_tokens, again.cache_write_tokens) == (400, 50)
