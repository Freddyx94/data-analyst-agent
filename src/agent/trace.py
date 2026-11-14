"""Writes every step of an agent run to a trace file.

Why: an agent makes several decisions per question, and the final answer does
not show them. The trace is the full record: what the model said, which tool it
asked for, what came back, how many tokens and how long. We use it to debug, to
evaluate (Stage 3) and to show the steps in the app (Stage 6).

Format: JSON Lines. One JSON object per line, one line per event, appended as
it happens. If a run crashes, everything up to that point is already on disk.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.config import load_config, project_path


def new_trace_path() -> Path:
    """A new file name in the traces folder: time first, so files sort by time."""
    folder = project_path(load_config()["paths"]["traces_dir"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return folder / f"{stamp}_{uuid.uuid4().hex[:6]}.jsonl"


class Tracer:
    """Collects the events of one run, in memory and (if a path is given) in a file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self.events: list[dict[str, Any]] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **data: Any) -> None:
        """Record one event, for example log("tool_result", name="run_sql", ...)."""
        record = {"event": event, "time": round(time.time(), 3), **data}
        self.events.append(record)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                # default=str: dates and other special values are written as text.
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_trace(path: Path | str) -> list[dict[str, Any]]:
    """Load a trace file back into a list of events."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
