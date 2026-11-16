"""Command-line chat with the agent.

    python -m agent.chat

Type a question and press Enter. Type "exit" (or press Enter on an empty line)
to stop. Every question is a new, separate run: the agent does not remember
the previous question. Each run writes a trace file; read the newest one with
    python -m agent.show_trace
"""

from __future__ import annotations

from agent.db import connect
from agent.llm import ModelError, make_model
from agent.loop import run_agent
from agent.trace import Tracer, new_trace_path


def main() -> None:
    try:
        model = make_model()
    except ModelError as error:
        print(f"Cannot start the chat: {error}")
        return

    con = connect()
    total_cost = 0.0
    print(f"Data analyst agent (model: {model.name}). Ask a question about the sales data.")
    print('Type "exit" to stop.\n')

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question or question.lower() in {"exit", "quit"}:
            break

        tracer = Tracer(new_trace_path())
        try:
            result = run_agent(question, model, con, tracer)
        except ModelError as error:
            print(f"\nThe model call failed: {error}\n")
            continue

        total_cost += result.cost_usd
        print(f"\nAgent: {result.answer}\n")
        if result.ungrounded_numbers:
            # The number check: these numbers are in no query or result of this run.
            print(
                "  WARNING: these numbers do not come from a query result in this run: "
                f"{', '.join(result.ungrounded_numbers)}. Treat them with care.\n"
            )
        print(
            f"  [{result.steps} steps, {result.input_tokens} tokens in, "
            f"{result.output_tokens} tokens out, ${result.cost_usd:.4f}; "
            f"trace: {result.trace_path.name}]\n"
        )

    con.close()
    print(f"Total cost of this chat: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
