"""Checks on the reporting store refresh and the finance rules it implements."""

import sqlite3

import pandas as pd

from reporting_assistant.refresh import last_refresh
from reporting_assistant.semantic import load_semantic_layer


def _read(settings, sql):
    con = sqlite3.connect(settings.reporting_db)
    try:
        return pd.read_sql_query(sql, con)
    finally:
        con.close()


def test_usd_conversion_uses_erp_average_rate(settings):
    frame = _read(settings, """
        SELECT g.amount_local, g.amount_usd, fx.avg_rate_to_usd FROM src_gl_monthly g
        JOIN src_fx_rates fx USING (fiscal_year, fiscal_period, currency) WHERE g.currency = 'PKR' LIMIT 50""")
    assert ((frame.amount_local * frame.avg_rate_to_usd).round(2) - frame.amount_usd).abs().max() < 0.011


def test_consolidation_excludes_intercompany(settings):
    lines = _read(settings, "SELECT DISTINCT report_line FROM src_gl_consolidated_monthly")["report_line"]
    assert "intercompany" not in set(lines)
    by_entity = _read(settings, "SELECT DISTINCT report_line FROM src_gl_monthly")["report_line"]
    assert "intercompany" in set(by_entity)


def test_open_and_closed_periods(settings):
    frame = _read(settings, "SELECT fiscal_year, fiscal_period, is_closed FROM src_fiscal_periods WHERE fiscal_year = 2027 AND fiscal_period IN (3, 4)")
    assert frame.set_index("fiscal_period")["is_closed"].to_dict() == {3: 1, 4: 0}


def test_refresh_log_records_both_domains(settings):
    assert set(last_refresh(settings.reporting_db)) == {"inventory", "finance"}


def test_every_view_maps_to_a_refreshed_table(settings):
    layer = load_semantic_layer(settings.semantic_layer_path)
    con = sqlite3.connect(settings.reporting_db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for view in layer.views.values():
        assert view.source in tables
        columns = {r[1] for r in con.execute(f"PRAGMA table_info({view.source})")}
        assert set(view.columns) <= columns, view.name
    con.close()
