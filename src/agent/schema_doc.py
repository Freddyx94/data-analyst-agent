"""Reads table and column descriptions from docs/schema.md.

Why: the database only stores names and types. What a column means (what SNAP
is, that prices are weekly) is written by a person in a Markdown file that is
easy to edit. This module turns that file into data the tools can use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent.config import load_config, project_path


@dataclass
class TableDoc:
    """The human-written description of one table and its columns."""

    description: str = ""
    columns: dict[str, str] = field(default_factory=dict)


def load_general_notes(path: Path | str | None = None) -> str:
    """Return the text of the "## General notes" section.

    Why: these lines are about the whole database (one store, where the data
    ends). No tool returns them, so the loop puts them into the system prompt.
    """
    doc_path = Path(path) if path else project_path(load_config()["paths"]["schema_doc"])
    notes: list[str] = []
    inside = False
    for line in doc_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "general notes"
        elif inside and line.strip():
            notes.append(line.strip())
    return "\n".join(notes)


def load_schema_doc(path: Path | str | None = None) -> dict[str, TableDoc]:
    """Parse docs/schema.md into {"schema.table": TableDoc}.

    Only sections with a heading like "## marts.dim_date" are tables. Other
    sections (format rules, general notes) are skipped here.
    """
    doc_path = Path(path) if path else project_path(load_config()["paths"]["schema_doc"])
    docs: dict[str, TableDoc] = {}
    current: TableDoc | None = None

    for line in doc_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text.startswith("## "):
            name = text[3:].strip().strip("`").lower()
            is_table = "." in name and " " not in name
            current = TableDoc() if is_table else None
            if current is not None:
                docs[name] = current
        elif current is None or not text:
            continue
        elif text.startswith("|"):
            cells = [cell.strip().strip("`") for cell in text.strip("|").split("|")]
            is_header = cells[0].lower() == "column"
            is_separator = set(cells[0]) <= set("-: ")
            if len(cells) >= 2 and not is_header and not is_separator:
                current.columns[cells[0]] = cells[1]
        else:
            current.description = f"{current.description} {text}".strip()

    return docs
