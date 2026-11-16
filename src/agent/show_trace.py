"""Prints a trace file in a form a person can read.

    python -m agent.show_trace                 the newest trace in traces/
    python -m agent.show_trace path/to/file    one specific trace
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agent.config import load_config, project_path
from agent.trace import read_trace

MAX_RESULT_LINES = 15  # long tool results are cut on screen; the file keeps everything


def format_trace(events: list[dict[str, Any]]) -> str:
    """Turn the list of events into readable text."""
    lines: list[str] = []
    for event in events:
        kind = event["event"]
        if kind == "question":
            lines.append(f"QUESTION: {event['question']}")
            lines.append(
                f"  model: {event['model']}   prompt: {event['system_prompt']}   "
                f"max steps: {event['max_steps']}"
            )
        elif kind == "model_reply":
            lines.append("")
            lines.append(
                f"--- step {event['step']}: model "
                f"({event['seconds']}s, {event['input_tokens']} tokens in, "
                f"{event['output_tokens']} tokens out, ${event.get('cost_usd', 0.0):.6f})"
            )
            if event["text"]:
                lines.append(_indent(event["text"], "  says: "))
            for call in event["tool_calls"]:
                arguments = ", ".join(f"{k}={v!r}" for k, v in call["arguments"].items())
                lines.append(f"  asks for tool: {call['name']}({arguments})")
        elif kind == "tool_result":
            result_lines = event["result"].splitlines()
            shown = result_lines[:MAX_RESULT_LINES]
            if len(result_lines) > MAX_RESULT_LINES:
                shown.append(f"... ({len(result_lines) - MAX_RESULT_LINES} more lines in the trace file)")
            lines.append(f"  result of {event['name']} ({event['seconds']}s):")
            lines.extend(f"      {line}" for line in shown)
        elif kind == "final":
            lines.append("")
            lines.append(
                f"FINISHED: {event['stopped']} after {event['steps']} steps, "
                f"{event['input_tokens']} tokens in, {event['output_tokens']} tokens out, "
                f"${event.get('cost_usd', 0.0):.6f}"
            )
            lines.append(_indent(event["answer"], "ANSWER: "))
            if event.get("ungrounded_numbers"):
                lines.append(f"NUMBER CHECK: not from a query result: {', '.join(event['ungrounded_numbers'])}")
    return "\n".join(lines)


def _indent(text: str, first_prefix: str) -> str:
    """Put a label before the first line and line up the following lines under it."""
    pad = " " * len(first_prefix)
    parts = text.splitlines() or [""]
    return "\n".join([first_prefix + parts[0]] + [pad + part for part in parts[1:]])


def newest_trace() -> Path | None:
    folder = project_path(load_config()["paths"]["traces_dir"])
    files = sorted(folder.glob("*.jsonl")) if folder.exists() else []
    return files[-1] if files else None


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_trace()
    if path is None or not path.exists():
        print("No trace found. Run the agent first, or give the path of a trace file.")
        return
    print(f"Trace: {path}\n")
    print(format_trace(read_trace(path)))


if __name__ == "__main__":
    main()
