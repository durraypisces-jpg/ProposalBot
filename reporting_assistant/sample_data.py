"""Builds a sample ERP database so the assistant can be demonstrated and tested
before the client's real systems are connected.

The data is deterministic for a given "today": three warehouses, 40 items, about
15 months of stock movements, and three legal entities in USD, PKR and AED with
monthly P&L, budgets, ERP exchange rates and an ERP-produced consolidation.
"""

from __future__ import annotations

import random
import sqlite3
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

FISCAL_START_MONTH = 7

SITES = [
    ("WH-NORTH", "North Distribution Centre", "North"),
    ("WH-CENTRAL", "Central Warehouse", "Central"),
    ("ST-SOUTH", "South Retail Store", "South"),
]

CATEGORIES = {
    "Electrical": ["Cable reel", "Circuit breaker", "LED panel", "Switch socket", "Extension board"],
    "Plumbing": ["PVC pipe", "Ball valve", "Water pump", "Pipe fitting", "Mixer tap"],
    "Tools": ["Cordless drill", "Spanner set", "Angle grinder", "Tool box", "Measuring tape"],
    "Safety": ["Safety helmet", "Work gloves", "Safety boots", "Hi-vis vest", "Ear defenders"],
    "Hardware": ["Hinge pack", "Door lock", "Screw box", "Anchor bolts", "Padlock"],
    "Paint": ["Emulsion 4L", "Primer 1L", "Varnish 1L", "Paint roller", "Brush set"],
    "Garden": ["Garden hose", "Lawn mower", "Pruning shears", "Wheelbarrow", "Sprinkler"],
    "Lighting": ["Floodlight", "Desk lamp", "Bulb pack", "Solar light", "Torch"],
}
SUPPLIERS = ["Apex Supplies", "Delta Traders", "Orion Imports", "Summit Wholesale"]

ENTITIES = [
    ("US01", "Group Holdings Inc.", "USD"),
    ("PK01", "Pakistan Trading (Pvt) Ltd", "PKR"),
    ("AE01", "Gulf Distribution FZE", "AED"),
]
ACCOUNTS = [
    ("4000", "Product sales", "revenue", "income"),
    ("4100", "Service revenue", "revenue", "income"),
    ("4900", "Intercompany management fees", "intercompany", "income"),
    ("5000", "Cost of goods sold", "cost_of_sales", "expense"),
    ("6000", "Salaries and wages", "operating_expenses", "expense"),
    ("6100", "Rent and utilities", "operating_expenses", "expense"),
    ("6200", "Marketing", "operating_expenses", "expense"),
    ("6300", "IT and software", "operating_expenses", "expense"),
    ("6400", "Travel", "operating_expenses", "expense"),
    ("5900", "Intercompany management charges", "intercompany", "expense"),
]
# (account, department, monthly USD-equivalent base for a scale-1.0 entity)
POSTING_PLAN = [
    ("4000", "Sales", 420_000), ("4100", "Sales", 60_000),
    ("5000", "Operations", 250_000),
    ("6000", "Sales", 38_000), ("6000", "Operations", 45_000), ("6000", "Finance", 22_000),
    ("6000", "IT", 18_000), ("6000", "Marketing", 15_000),
    ("6100", "Operations", 21_000), ("6100", "Finance", 4_000),
    ("6200", "Marketing", 26_000), ("6300", "IT", 14_000),
    ("6400", "Sales", 7_000), ("6400", "Marketing", 3_000),
]
ENTITY_SCALE = {"US01": 0.6, "PK01": 1.0, "AE01": 0.75}
FX_BASE = {"USD": 1.0, "PKR": 1 / 280.0, "AED": 1 / 3.6725}
MANAGEMENT_FEE_USD = {"PK01": 18_000, "AE01": 12_000}


def fiscal_of(day: date) -> tuple[int, int]:
    period = (day.month - FISCAL_START_MONTH) % 12 + 1
    year = day.year + (1 if day.month >= FISCAL_START_MONTH else 0)
    return year, period


def period_dates(fiscal_year: int, fiscal_period: int) -> tuple[date, date]:
    month = (FISCAL_START_MONTH - 1 + fiscal_period - 1) % 12 + 1
    year = fiscal_year - 1 if month >= FISCAL_START_MONTH else fiscal_year
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


SCHEMA = """
CREATE TABLE sites (site_id TEXT PRIMARY KEY, site_name TEXT, region TEXT);
CREATE TABLE products (sku TEXT PRIMARY KEY, product_name TEXT, category TEXT, unit_cost REAL,
                       reorder_level INTEGER, supplier TEXT);
CREATE TABLE stock_levels (snapshot_date TEXT, site_id TEXT, sku TEXT, qty_on_hand INTEGER);
CREATE TABLE stock_movements (movement_date TEXT, site_id TEXT, sku TEXT, movement_type TEXT,
                              qty INTEGER, unit_cost REAL);
CREATE TABLE entities (entity_id TEXT PRIMARY KEY, entity_name TEXT, currency TEXT);
CREATE TABLE accounts (account_code TEXT PRIMARY KEY, account_name TEXT, report_line TEXT,
                       account_type TEXT);
CREATE TABLE fiscal_periods (fiscal_year INTEGER, fiscal_period INTEGER, fiscal_quarter INTEGER,
                             start_date TEXT, end_date TEXT, is_closed INTEGER);
CREATE TABLE fx_rates (fiscal_year INTEGER, fiscal_period INTEGER, currency TEXT,
                       avg_rate_to_usd REAL, closing_rate_to_usd REAL);
CREATE TABLE gl_balances (fiscal_year INTEGER, fiscal_period INTEGER, entity_id TEXT,
                          department TEXT, account_code TEXT, amount_local REAL);
CREATE TABLE budgets (fiscal_year INTEGER, fiscal_period INTEGER, entity_id TEXT,
                      department TEXT, account_code TEXT, amount_local REAL);
CREATE TABLE consolidated_pl (fiscal_year INTEGER, fiscal_period INTEGER, account_code TEXT,
                              amount_usd REAL);
"""


def build_sample_erp(path: Path, today: date | None = None, seed: int = 42) -> Path:
    """Create (or replace) the sample ERP database at `path`."""
    today = today or date.today()
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)

    con.executemany("INSERT INTO sites VALUES (?,?,?)", SITES)
    products = []
    for c_index, (category, names) in enumerate(CATEGORIES.items()):
        for n_index, name in enumerate(names):
            sku = f"SKU-{c_index + 1}{n_index + 1:02d}"
            cost = round(rng.uniform(2, 180), 2)
            products.append((sku, name, category, cost, rng.choice([10, 15, 20, 25, 40]), rng.choice(SUPPLIERS)))
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", products)

    _build_inventory(con, rng, products, today)
    _build_finance(con, rng, today)
    con.commit()
    con.close()
    return path


def _build_inventory(con: sqlite3.Connection, rng: random.Random, products: list, today: date) -> None:
    start = date(today.year - 1, today.month, 1) - timedelta(days=92)
    # A handful of items stop selling 120 days ago so "slow-moving" has answers.
    stopped = {p[0] for p in rng.sample(products, 6)}
    stop_date = today - timedelta(days=120)
    movements, snapshots = [], []
    for site_id, *_ in SITES:
        site_factor = {"WH-NORTH": 1.4, "WH-CENTRAL": 1.0, "ST-SOUTH": 0.5}[site_id]
        for sku, _, _, cost, reorder, _ in products:
            demand = rng.uniform(0.5, 4.0) * site_factor
            qty = rng.randint(30, 200)
            day = start
            while day <= today:
                sells = not (sku in stopped and day >= stop_date)
                if sells and rng.random() < 0.35:
                    out = min(qty, max(1, int(rng.expovariate(1 / demand))))
                    if out:
                        qty -= out
                        movements.append((day.isoformat(), site_id, sku, "OUT", out, cost))
                if sells and qty <= reorder and rng.random() < 0.3:
                    received = rng.randint(reorder * 2, reorder * 6)
                    qty += received
                    movements.append((day.isoformat(), site_id, sku, "IN", received, cost))
                if rng.random() < 0.003:
                    adj = rng.choice([-3, -2, -1, 1, 2])
                    adj = max(adj, -qty)
                    qty += adj
                    movements.append((day.isoformat(), site_id, sku, "ADJ", adj, cost))
                next_day = day + timedelta(days=1)
                if next_day.month != day.month or day == today:
                    snapshots.append((day.isoformat(), site_id, sku, qty))
                day = next_day
    con.executemany("INSERT INTO stock_movements VALUES (?,?,?,?,?,?)", movements)
    con.executemany("INSERT INTO stock_levels VALUES (?,?,?,?)", snapshots)


def _build_finance(con: sqlite3.Connection, rng: random.Random, today: date) -> None:
    con.executemany("INSERT INTO entities VALUES (?,?,?)", ENTITIES)
    con.executemany("INSERT INTO accounts VALUES (?,?,?,?)", ACCOUNTS)

    current_fy, current_fp = fiscal_of(today)
    first_fy = current_fy - 2
    periods, fx, gl, budgets, consolidated = [], [], [], [], {}
    pkr_drift = 1.0
    for fiscal_year in range(first_fy, current_fy + 1):
        for fiscal_period in range(1, 13):
            start, end = period_dates(fiscal_year, fiscal_period)
            is_current = (fiscal_year, fiscal_period) == (current_fy, current_fp)
            is_closed = int(end < today and not is_current)
            periods.append((fiscal_year, fiscal_period, (fiscal_period - 1) // 3 + 1,
                            start.isoformat(), end.isoformat(), is_closed))

            pkr_drift *= rng.uniform(0.995, 1.002)
            rates = {"USD": 1.0, "PKR": FX_BASE["PKR"] * pkr_drift, "AED": FX_BASE["AED"]}
            for currency, rate in rates.items():
                fx.append((fiscal_year, fiscal_period, currency, round(rate, 8),
                           round(rate * rng.uniform(0.998, 1.002), 8)))

            if start > today:
                continue  # no actuals in the future; budgets below still cover the full year
            # The open period only has postings up to today.
            elapsed = 1.0 if not is_current else today.day / monthrange(today.year, today.month)[1]
            months_in = (fiscal_year - first_fy) * 12 + fiscal_period
            growth = 1 + 0.008 * months_in
            season = 1 + 0.12 * (1 if fiscal_period in (5, 6, 11, 12) else 0)
            for entity_id, _, currency in ENTITIES:
                rate = rates[currency]
                for account, department, base in POSTING_PLAN:
                    usd = base * ENTITY_SCALE[entity_id] * growth * elapsed * rng.uniform(0.9, 1.1)
                    if account in ("4000", "4100", "5000"):
                        usd *= season
                    local = round(usd / rate, 2)
                    gl.append((fiscal_year, fiscal_period, entity_id, department, account, local))
                    key = (fiscal_year, fiscal_period, account)
                    consolidated[key] = consolidated.get(key, 0.0) + local * rate
                if entity_id in MANAGEMENT_FEE_USD:
                    fee_usd = MANAGEMENT_FEE_USD[entity_id] * elapsed
                    gl.append((fiscal_year, fiscal_period, entity_id, "Finance", "5900", round(fee_usd / rate, 2)))
                    gl.append((fiscal_year, fiscal_period, "US01", "Finance", "4900", round(fee_usd, 2)))

    for fiscal_year in range(first_fy, current_fy + 1):
        for fiscal_period in range(1, 13):
            rates = {r[2]: r[3] for r in fx if r[0] == fiscal_year and r[1] == fiscal_period}
            months_in = (fiscal_year - first_fy) * 12 + fiscal_period
            for entity_id, _, currency in ENTITIES:
                for account, department, base in POSTING_PLAN:
                    usd = base * ENTITY_SCALE[entity_id] * (1 + 0.009 * months_in) * rng.uniform(0.95, 1.08)
                    budgets.append((fiscal_year, fiscal_period, entity_id, department, account,
                                    round(usd / rates[currency], 2)))

    con.executemany("INSERT INTO fiscal_periods VALUES (?,?,?,?,?,?)", periods)
    con.executemany("INSERT INTO fx_rates VALUES (?,?,?,?,?)", fx)
    con.executemany("INSERT INTO gl_balances VALUES (?,?,?,?,?,?)", gl)
    con.executemany("INSERT INTO budgets VALUES (?,?,?,?,?,?)", budgets)
    # The ERP's own consolidation: intercompany accounts (4900/5900) eliminate,
    # so they never reach the consolidated table (FR-17).
    con.executemany(
        "INSERT INTO consolidated_pl VALUES (?,?,?,?)",
        [(fy, fp, acc, round(amount, 2)) for (fy, fp, acc), amount in sorted(consolidated.items())],
    )
