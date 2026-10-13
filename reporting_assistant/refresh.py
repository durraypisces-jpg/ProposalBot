"""Reporting data store refresh (proposal section 7, new in v1.1).

Copies the ERP data into a separate SQLite file so reports never load the live
ERP. Run it on a schedule (hourly for inventory, daily for finance by default),
e.g. from cron: `python -m reporting_assistant.cli refresh`.

The copy is built in a temporary file and swapped in atomically, so a report
never reads a half-refreshed store.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Reporting table -> (domain, SELECT over the attached ERP database "erp").
TRANSFORMS: dict[str, tuple[str, str]] = {
    "src_sites": ("inventory", "SELECT site_id, site_name, region FROM erp.sites"),
    "src_products": ("inventory", "SELECT sku, product_name, category, unit_cost, reorder_level, supplier FROM erp.products"),
    "src_stock_levels": ("inventory", "SELECT snapshot_date, site_id, sku, qty_on_hand FROM erp.stock_levels"),
    "src_stock_movements": ("inventory", "SELECT movement_date, site_id, sku, movement_type, qty, unit_cost FROM erp.stock_movements"),
    "src_fiscal_periods": ("finance", "SELECT fiscal_year, fiscal_period, fiscal_quarter, start_date, end_date, is_closed FROM erp.fiscal_periods"),
    "src_entities": ("finance", "SELECT entity_id, entity_name, currency FROM erp.entities"),
    "src_accounts": ("finance", "SELECT account_code, account_name, report_line, account_type FROM erp.accounts"),
    "src_fx_rates": ("finance", "SELECT fiscal_year, fiscal_period, currency, avg_rate_to_usd, closing_rate_to_usd FROM erp.fx_rates"),
    # FR-16: converted with the ERP's own average rate for the period.
    "src_gl_monthly": ("finance", """
        SELECT g.fiscal_year, g.fiscal_period, g.entity_id, g.department, g.account_code,
               a.report_line, g.amount_local, e.currency,
               ROUND(g.amount_local * fx.avg_rate_to_usd, 2) AS amount_usd
        FROM erp.gl_balances g
        JOIN erp.accounts a ON a.account_code = g.account_code
        JOIN erp.entities e ON e.entity_id = g.entity_id
        JOIN erp.fx_rates fx ON fx.fiscal_year = g.fiscal_year AND fx.fiscal_period = g.fiscal_period
                            AND fx.currency = e.currency"""),
    "src_budget_monthly": ("finance", """
        SELECT b.fiscal_year, b.fiscal_period, b.entity_id, b.department, b.account_code,
               a.report_line, ROUND(b.amount_local * fx.avg_rate_to_usd, 2) AS amount_usd
        FROM erp.budgets b
        JOIN erp.accounts a ON a.account_code = b.account_code
        JOIN erp.entities e ON e.entity_id = b.entity_id
        JOIN erp.fx_rates fx ON fx.fiscal_year = b.fiscal_year AND fx.fiscal_period = b.fiscal_period
                            AND fx.currency = e.currency"""),
    # FR-17: the ERP's consolidation, taken as-is.
    "src_gl_consolidated_monthly": ("finance", """
        SELECT c.fiscal_year, c.fiscal_period, a.report_line, c.account_code, c.amount_usd
        FROM erp.consolidated_pl c JOIN erp.accounts a ON a.account_code = c.account_code"""),
}

INDEXES = [
    "CREATE INDEX ix_levels ON src_stock_levels (snapshot_date, site_id, sku)",
    "CREATE INDEX ix_moves ON src_stock_movements (movement_date, site_id, sku)",
    "CREATE INDEX ix_gl ON src_gl_monthly (fiscal_year, fiscal_period, entity_id)",
    "CREATE INDEX ix_budget ON src_budget_monthly (fiscal_year, fiscal_period, entity_id)",
]


def refresh_reporting_store(erp_db: Path, reporting_db: Path, now: datetime | None = None) -> dict[str, int]:
    """Rebuild the reporting store from the ERP. Returns row counts per table."""
    now = now or datetime.now(timezone.utc)
    reporting_db.parent.mkdir(parents=True, exist_ok=True)
    tmp = reporting_db.with_suffix(".refreshing")
    if tmp.exists():
        tmp.unlink()

    counts: dict[str, int] = {}
    # URI mode lets the ERP be attached read-only.
    con = sqlite3.connect(f"file:{tmp}", uri=True)
    try:
        con.execute("ATTACH DATABASE ? AS erp", (f"file:{erp_db}?mode=ro",))
        con.execute("CREATE TABLE refresh_log (table_name TEXT, domain TEXT, refreshed_at TEXT, row_count INTEGER)")
        for table, (domain, select) in TRANSFORMS.items():
            con.execute(f"CREATE TABLE {table} AS {select}")
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            con.execute("INSERT INTO refresh_log VALUES (?,?,?,?)", (table, domain, now.isoformat(timespec="seconds"), counts[table]))
        for statement in INDEXES:
            con.execute(statement)
        con.commit()
        con.execute("DETACH DATABASE erp")
    finally:
        con.close()
    os.replace(tmp, reporting_db)
    return counts


def last_refresh(reporting_db: Path) -> dict[str, str]:
    """The "data as of" time per domain (FR-10)."""
    con = sqlite3.connect(f"file:{reporting_db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT domain, MIN(refreshed_at) FROM refresh_log GROUP BY domain").fetchall()
    finally:
        con.close()
    return dict(rows)
