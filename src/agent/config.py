"""Loads config.yaml and builds paths from the project root.

Why: every setting (paths, limits, model names) lives in one file, and paths are
built from the project root so the code works from any working directory and on
any operating system.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# src/agent/config.py -> parents[2] is the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_config() -> dict[str, Any]:
    """Read config.yaml fresh on every call, so edits apply without a restart."""
    with open(PROJECT_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def project_path(relative: str) -> Path:
    """Turn a path from config.yaml (relative to the project root) into a full path."""
    return PROJECT_ROOT / relative


def load_env(path: Path | str | None = None) -> None:
    """Copy the NAME=value lines of the .env file into the environment.

    Why by hand: a .env file is only lines of NAME=value, so this needs no
    extra package. A value that is already set in the environment wins, so CI
    or a shell can override the file. The values are never printed or logged.
    """
    env_path = Path(path) if path else PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip().strip("'\"")
        if value:
            os.environ.setdefault(name.strip(), value)


def agent_settings(variant: str | None = None) -> dict[str, Any]:
    """The settings of one agent variant (prompt file, tools), plus its name.

    Why variants: when we change the agent we must still be able to run the old
    one exactly as it was, otherwise "before" and "after" cannot be compared.
    """
    agent = load_config()["agent"]
    name = variant or agent["variant"]
    if name not in agent["variants"]:
        raise ValueError(f"Unknown agent variant {name!r}. Known: {list(agent['variants'])}")
    return {"name": name, "max_steps": agent["max_steps"], **agent["variants"][name]}
