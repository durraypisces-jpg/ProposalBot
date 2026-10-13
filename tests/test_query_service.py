from datetime import date

import pytest

from reporting_assistant.auth import demo_user
from reporting_assistant.query_service import QueryFailed, QueryRejected, QueryService

STOCK_BY_SITE = "SELECT site_id, COUNT(*) AS n FROM stock_levels GROUP BY site_id"


@pytest.fixture
def queries(settings, layer):
    return QueryService(settings.reporting_db, layer, timeout_seconds=5, max_rows=100)


def test_fiscal_calendar(layer):
    assert layer.fiscal_period_of(date(2026, 10, 13)) == (2027, 4)
    assert layer.fiscal_period_of(date(2026, 6, 30)) == (2026, 12)
    assert layer.fiscal_period_of(date(2025, 7, 1)) == (2026, 1)


def test_warehouse_manager_sees_only_own_site(queries, layer):
    result = queries.run(STOCK_BY_SITE, demo_user(layer, "amina"))
    assert result.frame["site_id"].tolist() == ["WH-NORTH"]


def test_row_filter_cannot_be_bypassed_with_a_where_clause(queries, layer):
    result = queries.run("SELECT * FROM stock_levels WHERE site_id = 'ST-SOUTH'", demo_user(layer, "amina"))
    assert result.frame.empty


def test_ops_lead_sees_all_sites(queries, layer):
    result = queries.run(STOCK_BY_SITE, demo_user(layer, "omar"))
    assert sorted(result.frame["site_id"]) == ["ST-SOUTH", "WH-CENTRAL", "WH-NORTH"]


def test_finance_user_limited_to_own_entity(queries, layer):
    result = queries.run("SELECT DISTINCT entity_id FROM gl_monthly", demo_user(layer, "sara"))
    assert result.frame["entity_id"].tolist() == ["PK01"]


def test_finance_user_cannot_query_inventory_or_consolidated(queries, layer):
    sara = demo_user(layer, "sara")
    with pytest.raises(QueryRejected, match="stock_levels"):
        queries.run(STOCK_BY_SITE, sara)
    with pytest.raises(QueryRejected, match="gl_consolidated_monthly"):
        queries.run("SELECT * FROM gl_consolidated_monthly", sara)


def test_inventory_role_cannot_query_finance(queries, layer):
    with pytest.raises(QueryRejected):
        queries.run("SELECT * FROM gl_monthly", demo_user(layer, "omar"))


@pytest.mark.parametrize("sql", [
    "DELETE FROM products",
    "UPDATE products SET unit_cost = 0",
    "PRAGMA table_list",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT * FROM src_products",
    "SELECT * FROM main.src_gl_monthly",
    "SELECT * FROM sqlite_master",
    "SELECT 1; SELECT 2",
])
def test_guardrails_reject_unsafe_sql(queries, layer, sql):
    with pytest.raises(QueryRejected):
        queries.run(sql, demo_user(layer, "admin"))


def test_ctes_and_comments_are_allowed(queries, layer):
    sql = "-- top sites\nWITH s AS (SELECT site_id FROM sites) SELECT COUNT(*) AS n FROM s;"
    assert queries.run(sql, demo_user(layer, "omar")).frame["n"].tolist() == [3]


def test_time_limit_stops_runaway_queries(settings, layer):
    slow = QueryService(settings.reporting_db, layer, timeout_seconds=0.2)
    sql = "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(QueryFailed, match="longer than"):
        slow.run(sql, demo_user(layer, "admin"))


def test_row_limit_truncates(queries, layer):
    result = queries.run("SELECT * FROM stock_movements", demo_user(layer, "omar"))
    assert len(result.frame) == 100 and result.truncated


def test_sql_errors_are_reported_for_repair(queries, layer):
    with pytest.raises(QueryFailed, match="no such column"):
        queries.run("SELECT nonexistent FROM products", demo_user(layer, "omar"))


def test_row_counts_of_filtered_tables_do_not_leak(queries, layer):
    with pytest.raises(QueryRejected):
        queries.run("SELECT COUNT(*) FROM src_stock_levels", demo_user(layer, "amina"))
    with pytest.raises(QueryRejected):
        queries.run("SELECT COUNT(*) FROM src_gl_monthly", demo_user(layer, "omar"))
