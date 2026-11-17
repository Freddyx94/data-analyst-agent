"""Runs the Streamlit app headless with the fake model and the fixture database: no key, no cost."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app" / "streamlit_app.py")
SCRIPT = ROOT / "tests" / "fixtures" / "scripts" / "units_by_department.json"


@pytest.fixture
def app(monkeypatch, fixture_db_path) -> AppTest:
    monkeypatch.setenv("AGENT_DATABASE", str(fixture_db_path))
    monkeypatch.setenv("AGENT_FAKE_SCRIPT", str(SCRIPT))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def test_app_starts_without_errors(app):
    assert not app.exception
    assert app.title[0].value == "Ask the sales data"
    assert len(app.chat_input) == 1


def test_a_question_shows_answer_table_chart_sql_and_steps(app):
    app.chat_input[0].set_value("How many units did each department sell?").run()
    assert not app.exception

    texts = " ".join(m.value for m in app.markdown)
    assert "FOODS_3 sold the most with 29 units" in texts
    assert len(app.dataframe) == 1
    table = app.dataframe[0].value
    assert list(table.columns) == ["dept_id", "units"] and len(table) == 3
    assert len(app.get("vega_lite_chart")) == 1  # the bar chart for three departments

    labels = [e.label for e in app.expander]
    assert labels == ["SQL used", "Show steps"]
    code = [c.value for c in app.code]
    assert any("SELECT i.dept_id" in c for c in code)
    assert any("ERROR: Table dim_item must be written with its schema" in c for c in code)  # the trace

    sidebar_metrics = {m.label: m.value for m in app.sidebar.metric}
    assert sidebar_metrics["Steps"] == "5"


def test_dollar_signs_stay_text(monkeypatch, fixture_db_path, tmp_path):
    """Streamlit would read "$251,336 and $244,394" as a formula; the app must escape the signs."""
    script = tmp_path / "dollars.json"
    script.write_text('[{"text": "May had $251,336 and August $244,394."}]', encoding="utf-8")
    monkeypatch.setenv("AGENT_DATABASE", str(fixture_db_path))
    monkeypatch.setenv("AGENT_FAKE_SCRIPT", str(script))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.chat_input[0].set_value("Which month had the highest revenue?").run()
    assert "May had \\$251,336 and August \\$244,394." in [m.value for m in at.markdown]


def test_without_a_key_the_app_explains_what_to_do(monkeypatch, fixture_db_path):
    monkeypatch.setenv("AGENT_DATABASE", str(fixture_db_path))
    monkeypatch.delenv("AGENT_FAKE_SCRIPT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("agent.llm.load_env", lambda: None)  # ignore a real .env file
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    assert "No API key found" in at.error[0].value
