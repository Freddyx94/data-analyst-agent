"""Run the agent loop once with the fake model and print the trace.

    python scripts/demo_fake_agent.py

No API key, no cost, no real data: the "model" replays the answers in
tests/fixtures/scripts/units_by_department.json, and the database is the tiny
fixture with invented rows. The loop, the tools, the guardrails and the trace
are the real code. Use it to see how one question moves through the loop.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from agent.config import PROJECT_ROOT
from agent.db import connect
from agent.llm import FakeModel
from agent.loop import run_agent
from agent.show_trace import format_trace
from agent.trace import Tracer, new_trace_path, read_trace

sys.path.insert(0, str(PROJECT_ROOT / "tests"))
from fixtures.make_fixture import build_fixture  # noqa: E402


def main() -> None:
    database = build_fixture(Path(tempfile.mkdtemp()) / "fixture.duckdb")
    model = FakeModel.from_file(
        PROJECT_ROOT / "tests" / "fixtures" / "scripts" / "units_by_department.json"
    )
    tracer = Tracer(new_trace_path())

    con = connect(database)
    result = run_agent("How many units did each department sell?", model, con, tracer)
    con.close()

    print(format_trace(read_trace(result.trace_path)))
    print(f"\nTrace file: {result.trace_path}")


if __name__ == "__main__":
    main()
