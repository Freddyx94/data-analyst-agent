"""Opens the DuckDB database in a way that cannot change or leak anything.

Why: this is the lowest safety layer. Even if every other check failed, the
database engine itself refuses writes and file access on this connection.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from agent.config import load_config, project_path


def connect(db_path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """Open the database read-only, with file and network access switched off.

    read_only=True: DuckDB rejects INSERT, UPDATE, DELETE, CREATE and DROP.
    enable_external_access=False: a SELECT cannot read files from disk
    (for example read_csv('/some/file')) or load data from the internet.
    This setting cannot be switched back on while the database is open.
    """
    path = Path(db_path) if db_path else project_path(load_config()["paths"]["database"])
    if not path.exists():
        # Without this check DuckDB's own error is harder to understand.
        raise FileNotFoundError(f"Database file not found: {path}")
    return duckdb.connect(
        str(path), read_only=True, config={"enable_external_access": False}
    )
