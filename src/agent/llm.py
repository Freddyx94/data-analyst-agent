"""The one small interface between the agent and a language model.

Why: the loop should not care which model answers. Every backend (Anthropic,
Ollama, the fake model for tests) does the same job: it gets the system prompt,
the conversation so far and the list of tools, and returns a ModelReply. The
loop only ever sees ModelReply, so swapping models changes nothing else.

The conversation is a list of plain dicts in our own neutral format:
    {"role": "user", "text": "..."}                    the question
    {"role": "assistant", "reply": ModelReply}         what the model said
    {"role": "tool_results", "results": [ToolResult]}  what our tools returned
Each backend translates this into the format its API wants.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agent.config import load_config, load_env


@dataclass
class ToolCall:
    """The model asks us to run one tool. It is a request, not an action."""

    id: str                    # links the result back to this request
    name: str                  # which tool
    arguments: dict[str, Any]  # the inputs the model chose


@dataclass
class ToolResult:
    """What our code returns to the model after running a tool."""

    call_id: str
    name: str
    content: str


@dataclass
class ModelReply:
    """One answer from the model: text, tool requests, or both."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    # The reply exactly as the provider sent it. Some APIs want it sent back
    # unchanged on the next call, so the backend keeps it here.
    provider_content: Any = None


class ModelError(Exception):
    """The model could not be reached or refused the request (bad key, no network, ...).

    Why our own error type: the chat and the evaluation should not need to know
    which provider is behind the interface. Each backend turns its provider's
    errors into this one, with a message a person can act on.
    """


class Model(Protocol):
    """Anything with this method can drive the agent loop."""

    name: str

    def reply(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply: ...


class FakeModel:
    """A model that replays answers written in advance.

    Why: tests and CI must not need an API key or cost money, and a test needs
    the same answer every time. The fake model also records what it was sent,
    so a test can check that tool results really reached the model.
    """

    name = "fake"

    def __init__(self, script: list[ModelReply]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []  # everything the loop sent us

    @classmethod
    def from_file(cls, path: Path | str) -> "FakeModel":
        """Load a script from JSON: a list of {"text": ..., "tool_calls": [...]}."""
        steps = json.loads(Path(path).read_text(encoding="utf-8"))
        script = []
        for step_number, step in enumerate(steps, start=1):
            calls = [
                ToolCall(id=f"call_{step_number}_{i}", name=c["name"], arguments=c["arguments"])
                for i, c in enumerate(step.get("tool_calls", []), start=1)
            ]
            script.append(ModelReply(text=step.get("text", ""), tool_calls=calls))
        return cls(script)

    def reply(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self._script:
            raise RuntimeError("FakeModel has no scripted answers left.")
        return self._script.pop(0)


# --- Anthropic backend -------------------------------------------------------


def cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one call in US dollars: tokens times the price from config.yaml.

    A token is a piece of a word (roughly 3 to 4 characters of English). The
    API charges separately for tokens we send (input) and tokens the model
    writes (output, which includes its thinking).
    """
    prices = load_config()["llm"]["prices_usd_per_million_tokens"].get(model_name)
    if prices is None:
        raise ModelError(
            f"No price for model {model_name!r} in config.yaml "
            "(llm.prices_usd_per_million_tokens). Add it so the cost can be tracked."
        )
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Our neutral tool list in the shape the Anthropic API expects."""
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in tools
    ]


def to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Our neutral conversation in the shape the Anthropic API expects.

    The API knows two roles, "user" and "assistant". Tool results are sent as
    a "user" message, because they come from our side of the conversation.
    """
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "user":
            converted.append({"role": "user", "content": message["text"]})
        elif role == "assistant":
            reply: ModelReply = message["reply"]
            # Send the model's own reply back exactly as it came (it can hold
            # thinking blocks the API wants unchanged). Rebuild it only when the
            # reply did not come from this API.
            content = reply.provider_content
            if content is None:
                content = [{"type": "text", "text": reply.text}] if reply.text else []
                content += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in reply.tool_calls
                ]
            converted.append({"role": "assistant", "content": content})
        elif role == "tool_results":
            # All results of one step go into one message; tool_use_id says
            # which request each result belongs to.
            blocks = []
            for result in message["results"]:
                block = {"type": "tool_result", "tool_use_id": result.call_id, "content": result.content}
                if result.content.startswith("ERROR"):
                    block["is_error"] = True
                blocks.append(block)
            converted.append({"role": "user", "content": blocks})
        else:
            raise ValueError(f"Unknown message role: {role!r}")
    return converted


def parse_anthropic_response(response: Any, model_name: str) -> ModelReply:
    """Turn the API's answer into our ModelReply.

    The answer is a list of blocks. A "text" block is something the model says,
    a "tool_use" block is a request to run a tool. Other blocks (thinking) are
    kept in provider_content but not shown.
    """
    text = "\n".join(block.text for block in response.content if block.type == "text").strip()
    calls = [
        ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
        for block in response.content
        if block.type == "tool_use"
    ]

    # stop_reason says why the model stopped. Two cases need care:
    if response.stop_reason == "refusal":
        # A safety system declined the request. There is no answer to use.
        text, calls = "The model declined to answer this request.", []
    elif response.stop_reason == "max_tokens":
        # The reply was cut off. A half-written tool request cannot be run.
        text, calls = f"{text}\n[The reply was cut off at the output token limit.]".strip(), []

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    return ModelReply(
        text=text,
        tool_calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd(model_name, input_tokens, output_tokens),
        provider_content=response.content,
    )


class AnthropicModel:
    """Talks to a Claude model through the Anthropic API."""

    def __init__(self, model_name: str) -> None:
        import anthropic  # imported here so tests with the fake model never need it

        load_env()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ModelError(
                "No API key found. Copy .env.example to .env and put your key after "
                "ANTHROPIC_API_KEY= (you get a key at https://console.anthropic.com)."
            )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        self._max_tokens = load_config()["llm"]["max_output_tokens"]
        self.name = model_name

    def reply(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply:
        anthropic = self._anthropic
        try:
            response = self._client.messages.create(
                model=self.name,
                max_tokens=self._max_tokens,
                system=system,
                tools=to_anthropic_tools(tools),
                messages=to_anthropic_messages(messages),
            )
        # Most specific error first. The SDK already retried rate limits and
        # server errors twice before we get here.
        except anthropic.AuthenticationError as error:
            raise ModelError("The API key was rejected. Check ANTHROPIC_API_KEY in .env.") from error
        except anthropic.PermissionDeniedError as error:
            raise ModelError(f"The API key may not use model {self.name}.") from error
        except anthropic.NotFoundError as error:
            raise ModelError(f"Model {self.name!r} was not found. Check llm.anthropic_model in config.yaml.") from error
        except anthropic.RateLimitError as error:
            raise ModelError("Rate limit reached. Wait a minute and try again.") from error
        except anthropic.APIStatusError as error:
            raise ModelError(f"The API returned an error ({error.status_code}): {error.message}") from error
        except anthropic.APIConnectionError as error:
            raise ModelError("Could not reach the API. Check the internet connection.") from error
        return parse_anthropic_response(response, self.name)


def make_model() -> Model:
    """Create the model that config.yaml asks for."""
    settings = load_config()["llm"]
    backend = settings["backend"]
    if backend == "anthropic":
        return AnthropicModel(settings["anthropic_model"])
    raise ModelError(f"Backend {backend!r} is not available yet. Use 'anthropic' (Ollama comes in Stage 3).")
