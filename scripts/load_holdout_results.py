"""One-off setup: load the hold-out results (model accuracy) into the database.

Run once, by hand:
    python scripts/load_holdout_results.py

Why this is a script and not an agent tool: the agent only ever gets a
read-only connection (agent.db.connect). Changing the database is a job for a
person. This script opens its own writable connection, adds one small table
(results.holdout_results) and closes again. Running it twice is safe: the
table is replaced.
"""

from __future__ import annotations

import csv
import io
import urllib.request
from pathlib import Path

import duckdb

from agent.config import load_config, project_path

URL = (
    "https://raw.githubusercontent.com/Freddyx94/retail-demand-forecasting/"
    "main/results/holdout_results.csv"
)

# The header we expect in the CSV, in order. The file comes from the internet,
# so we do not trust its column names: if the header is different, we stop.
EXPECTED_COLUMNS = [
    "model",
    "mae",
    "rmse",
    "rmsse",
    "weighted_rmsse",
    "bias",
    "rmsse_clean",
    "bias_clean",
    "rmsse_better_than_seasonal_naive_%",
    "rmsse_better_than_moving_average_28_%",
    "rmsse_better_than_ets_%",
]


def table_column(csv_column: str) -> str:
    """Column name used in the database: '_%' becomes '_pct'.

    Why: a % in a column name must be quoted in every SQL query, which is an
    easy mistake for the agent to make. All other names stay as in the CSV.
    """
    return csv_column.replace("_%", "_pct")


def download(url: str = URL) -> str:
    """Fetch the CSV as text."""
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8")


def parse(csv_text: str) -> list[tuple[str | float, ...]]:
    """Turn the CSV text into rows: (model name, then one number per metric)."""
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader, [])
    if header != EXPECTED_COLUMNS:
        raise ValueError(
            f"Unexpected CSV header.\n  expected: {EXPECTED_COLUMNS}\n  found:    {header}"
        )
    rows = []
    for line in reader:
        if not line:
            continue
        if len(line) != len(EXPECTED_COLUMNS):
            raise ValueError(f"Row has {len(line)} values, expected {len(EXPECTED_COLUMNS)}: {line}")
        rows.append((line[0], *(float(value) for value in line[1:])))
    if not rows:
        raise ValueError("The CSV has no data rows.")
    return rows


def load(rows: list[tuple[str | float, ...]], db_path: Path | str) -> None:
    """Create results.holdout_results and insert the rows (replacing an old table)."""
    names = [table_column(column) for column in EXPECTED_COLUMNS]
    column_sql = ", ".join(
        f"{name} {'VARCHAR' if name == 'model' else 'DOUBLE'}" for name in names
    )
    placeholders = ", ".join("?" for _ in names)

    con = duckdb.connect(str(db_path))  # writable: only this script does this
    try:
        con.execute("BEGIN")
        con.execute("CREATE SCHEMA IF NOT EXISTS results")
        con.execute(f"CREATE OR REPLACE TABLE results.holdout_results ({column_sql})")
        con.executemany(f"INSERT INTO results.holdout_results VALUES ({placeholders})", rows)
        con.execute("COMMIT")
    finally:
        con.close()


def main() -> None:
    db_path = project_path(load_config()["paths"]["database"])
    csv_text = download()
    rows = parse(csv_text)

    # Keep the downloaded file next to the database (data/ is git-ignored),
    # so it is clear where the table came from.
    (db_path.parent / "holdout_results.csv").write_text(csv_text, encoding="utf-8")

    load(rows, db_path)
    print(f"Loaded {len(rows)} rows into results.holdout_results in {db_path}")


if __name__ == "__main__":
    main()
