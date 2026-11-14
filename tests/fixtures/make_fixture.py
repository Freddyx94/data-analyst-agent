"""Builds a tiny database with the same tables and columns as the real one.

Why: the real M5 data may not be shared, and tests should run anywhere (also in
CI) in a second. All rows here are invented. The numbers follow a fixed rule,
so the database is identical every time it is built.

Run it by hand to get a file you can explore without the real data:
    python tests/fixtures/make_fixture.py
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb

FIRST_DAY = date(2015, 12, 19)  # a Saturday, so Walmart weeks line up
SALES_DAYS = 11                 # days with sales rows
FORECAST_DAYS = 3               # days after that, with forecasts only

# item_id, dept_id, launch offset in days from FIRST_DAY, price
ITEMS = [
    ("FOODS_1_001", "FOODS_1", 0, 2.00),
    ("FOODS_2_001", "FOODS_2", 0, 3.50),
    ("FOODS_3_001", "FOODS_3", 0, 1.25),
    ("FOODS_3_002", "FOODS_3", 7, 4.00),  # launched one week later
]
EVENTS = {date(2015, 12, 25): ("Christmas", "National")}
CLOSED = {date(2015, 12, 25)}


def build_fixture(path: Path | str) -> Path:
    """Create the fixture database at `path`, replacing an existing file."""
    path = Path(path)
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE SCHEMA marts; CREATE SCHEMA serving; CREATE SCHEMA raw;"
        "CREATE SCHEMA ml; CREATE SCHEMA results;"
    )
    con.execute("""
        CREATE TABLE ml.stockout_days (
            item_id VARCHAR, date DATE, run_start DATE, run_end DATE, run_days BIGINT);
        -- Two days of an invented 29-day run that began before the fixture period.
        INSERT INTO ml.stockout_days VALUES
            ('FOODS_1_001', DATE '2015-12-24', DATE '2015-11-27', DATE '2015-12-25', 29),
            ('FOODS_1_001', DATE '2015-12-25', DATE '2015-11-27', DATE '2015-12-25', 29);
        CREATE TABLE results.holdout_results (
            model VARCHAR, mae DOUBLE, rmse DOUBLE, rmsse DOUBLE, weighted_rmsse DOUBLE,
            bias DOUBLE, rmsse_clean DOUBLE, bias_clean DOUBLE,
            rmsse_better_than_seasonal_naive_pct DOUBLE,
            rmsse_better_than_moving_average_28_pct DOUBLE,
            rmsse_better_than_ets_pct DOUBLE);
        INSERT INTO results.holdout_results VALUES
            ('lightgbm', 1.0, 2.0, 0.70, 0.72, -0.05, 0.71, -0.05, 30.0, 12.5, 6.67),
            ('seasonal_naive', 1.5, 3.0, 1.00, 1.05, -0.06, 1.01, -0.06, 0.0, -25.0, -33.33),
            ('moving_average_28', 1.2, 2.4, 0.80, 0.83, -0.06, 0.81, -0.06, 20.0, 0.0, -6.67),
            ('ets', 1.1, 2.2, 0.75, 0.78, -0.06, 0.76, -0.06, 25.0, 6.25, 0.0),
            ('lightgbm_drop_stockouts', 1.05, 2.0, 0.71, 0.72, 0.02, 0.70, 0.01, 29.0, 11.25, 5.33);
        -- Not on the allowed list, like the real model-input table.
        CREATE TABLE ml.feat_daily_sales (item_id VARCHAR, date DATE, mean_7d DOUBLE);
        INSERT INTO ml.feat_daily_sales VALUES ('FOODS_1_001', DATE '2015-12-24', 99.5);
    """)
    con.execute("""
        CREATE TABLE marts.dim_date (
            date DATE, d VARCHAR, wm_yr_wk BIGINT, weekday VARCHAR, wday BIGINT,
            month BIGINT, year BIGINT, is_event BOOLEAN, event_name VARCHAR,
            event_type VARCHAR, is_snap BOOLEAN, is_store_closed BOOLEAN);
        CREATE TABLE marts.dim_item (
            item_id VARCHAR, dept_id VARCHAR, cat_id VARCHAR, launch_week BIGINT,
            launch_date DATE, first_sale_date DATE);
        CREATE TABLE marts.dim_store (store_id VARCHAR, state_id VARCHAR);
        CREATE TABLE marts.fct_daily_sales (
            date DATE, item_id VARCHAR, store_id VARCHAR, units_sold BIGINT,
            sell_price DOUBLE);
        CREATE TABLE serving.forecasts (
            item_id VARCHAR, date DATE, forecast DOUBLE, model_version VARCHAR,
            created_at TIMESTAMP);
        -- Not on the allowed list: tests check that the tools cannot read it.
        CREATE TABLE raw.secret_notes (note VARCHAR);
    """)

    days = [FIRST_DAY + timedelta(days=i) for i in range(SALES_DAYS + FORECAST_DAYS)]
    for i, day in enumerate(days):
        event_name, event_type = EVENTS.get(day, (None, None))
        con.execute(
            "INSERT INTO marts.dim_date VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [day, f"d_{i + 1}", _week(day), day.strftime("%A"), (day.weekday() + 2) % 7 + 1,
             day.month, day.year, event_name is not None, event_name, event_type,
             day.day <= 10, day in CLOSED],
        )

    con.execute("INSERT INTO marts.dim_store VALUES ('CA_1', 'CA')")
    con.execute("INSERT INTO raw.secret_notes VALUES ('the agent must never see this')")

    for item_number, (item_id, dept_id, launch_offset, price) in enumerate(ITEMS):
        launch = FIRST_DAY + timedelta(days=launch_offset)
        first_sale = None
        for i, day in enumerate(days[:SALES_DAYS]):
            if day < launch:
                continue
            # Invented but fixed: a small number from 0 to 4, and 0 when closed.
            units = 0 if day in CLOSED else (item_number * 3 + i) % 5
            if units > 0 and first_sale is None:
                first_sale = day
            con.execute(
                "INSERT INTO marts.fct_daily_sales VALUES (?, ?, 'CA_1', ?, ?)",
                [day, item_id, units, price],
            )
        con.execute(
            "INSERT INTO marts.dim_item VALUES (?, ?, 'FOODS', ?, ?, ?)",
            [item_id, dept_id, _week(launch), launch, first_sale],
        )
        for day in days[SALES_DAYS:]:
            con.execute(
                "INSERT INTO serving.forecasts VALUES (?, ?, ?, 'fixture_model', TIMESTAMP '2016-01-01 00:00:00')",
                [item_id, day, 1.5 + item_number * 0.25],
            )

    con.close()
    return path


def _week(day: date) -> int:
    """An invented Walmart week id: a new week starts every Saturday."""
    return 11547 + (day - FIRST_DAY).days // 7


if __name__ == "__main__":
    target = Path(__file__).parent / "fixture.duckdb"
    print(f"Built {build_fixture(target)}")
