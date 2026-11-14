"""Shared pytest setup: every test gets a read-only connection to the fixture database."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import duckdb
import pytest

from agent.db import connect
from fixtures.make_fixture import build_fixture


@pytest.fixture(scope="session")
def fixture_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the fixture database once per test run, in a temporary folder."""
    return build_fixture(tmp_path_factory.mktemp("db") / "fixture.duckdb")


@pytest.fixture
def con(fixture_db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open it the same way the agent does: through agent.db.connect()."""
    connection = connect(fixture_db_path)
    yield connection
    connection.close()
