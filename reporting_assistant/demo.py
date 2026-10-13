"""Offline demo planner: answers the standard report templates with prepared
plans, so the portal can be shown without an API key (RA_DEMO_MODE=1).

Free-form requests need the Claude planner.
"""

from __future__ import annotations

from .llm import Plan

LATEST = "(SELECT MAX(snapshot_date) FROM stock_levels)"
LAST_CLOSED = "(SELECT MAX(fiscal_year * 100 + fiscal_period) FROM fiscal_periods WHERE is_closed = 1)"
CURRENT_FY = "(SELECT fiscal_year FROM fiscal_periods WHERE date('now') BETWEEN start_date AND end_date)"


def _plan(**fields) -> Plan:
    base = dict(action="query", message="", series="", label_column="", period_column="", variance_pair=[],
                date_start="", date_end="", domain="inventory")
    base.update(fields)
    return Plan(**base)


DEMO_PLANS: dict[str, Plan] = {
    "Show current stock value by site as a bar chart": _plan(
        title="Stock value by site (latest snapshot)",
        sql=f"""SELECT s.site_name, ROUND(SUM(l.qty_on_hand * p.unit_cost), 2) AS stock_value_usd,
       SUM(l.qty_on_hand) AS units_on_hand
FROM stock_levels l JOIN products p ON p.sku = l.sku JOIN sites s ON s.site_id = l.site_id
WHERE l.snapshot_date = {LATEST}
GROUP BY s.site_name ORDER BY stock_value_usd DESC""",
        chart_type="bar", x="site_name", y=["stock_value_usd"], measures=["stock_value_usd", "units_on_hand"],
        label_column="site_name", filters_summary="Latest stock snapshot; all sites you can see; USD at standard cost"),
    "List items at or below their reorder level today, by site": _plan(
        title="Reorder alerts (today)",
        sql=f"""SELECT l.site_id, p.sku, p.product_name, l.qty_on_hand, p.reorder_level, p.supplier
FROM stock_levels l JOIN products p ON p.sku = l.sku
WHERE l.snapshot_date = {LATEST} AND l.qty_on_hand <= p.reorder_level
ORDER BY l.site_id, l.qty_on_hand""",
        chart_type="table", x="sku", y=["qty_on_hand"], measures=["qty_on_hand"], label_column="product_name",
        filters_summary="Latest snapshot; items at or below reorder level"),
    "Which items have had no sales in the last 90 days, and what is their stock value?": _plan(
        title="Slow-moving stock (no sales in 90 days)",
        sql=f"""SELECT p.sku, p.product_name, p.category, SUM(l.qty_on_hand) AS units_on_hand,
       ROUND(SUM(l.qty_on_hand * p.unit_cost), 2) AS stock_value_usd
FROM products p JOIN stock_levels l ON l.sku = p.sku AND l.snapshot_date = {LATEST}
WHERE NOT EXISTS (SELECT 1 FROM stock_movements m WHERE m.sku = p.sku AND m.site_id = l.site_id
                  AND m.movement_type = 'OUT' AND m.movement_date >= date('now', '-90 days'))
GROUP BY p.sku, p.product_name, p.category ORDER BY stock_value_usd DESC""",
        chart_type="bar", x="product_name", y=["stock_value_usd"], measures=["stock_value_usd", "units_on_hand"],
        label_column="product_name", filters_summary="No OUT movements in the last 90 days at the site holding the stock"),
    "Show units received and issued per month for the last 12 months": _plan(
        title="Units received and issued per month",
        sql="""SELECT strftime('%Y-%m', movement_date) AS month,
       SUM(CASE WHEN movement_type = 'IN' THEN qty ELSE 0 END) AS units_received,
       SUM(CASE WHEN movement_type = 'OUT' THEN qty ELSE 0 END) AS units_issued
FROM stock_movements
WHERE movement_date >= date('now', 'start of month', '-12 months')
  AND movement_date < date('now', 'start of month')
GROUP BY month ORDER BY month""",
        chart_type="line", x="month", y=["units_received", "units_issued"], measures=["units_received", "units_issued"],
        label_column="month", period_column="month", filters_summary="Last 12 complete calendar months; all sites you can see"),
    "Give me a P&L summary for the last closed period: revenue, cost of sales, operating expenses and operating profit": _plan(
        title="P&L summary, last closed period", domain="finance",
        sql=f"""SELECT printf('FY%d-P%02d', fiscal_year, fiscal_period) AS fiscal_period_label,
       ROUND(SUM(CASE WHEN report_line = 'revenue' THEN amount_usd END), 2) AS revenue_usd,
       ROUND(SUM(CASE WHEN report_line = 'cost_of_sales' THEN amount_usd END), 2) AS cost_of_sales_usd,
       ROUND(SUM(CASE WHEN report_line = 'operating_expenses' THEN amount_usd END), 2) AS operating_expenses_usd,
       ROUND(SUM(CASE WHEN report_line = 'revenue' THEN amount_usd WHEN report_line IN ('cost_of_sales', 'operating_expenses') THEN -amount_usd END), 2) AS operating_profit_usd
FROM gl_monthly
WHERE fiscal_year * 100 + fiscal_period = {LAST_CLOSED}
GROUP BY fiscal_year, fiscal_period""",
        chart_type="table", x="fiscal_period_label", y=["revenue_usd"],
        measures=["revenue_usd", "cost_of_sales_usd", "operating_expenses_usd", "operating_profit_usd"],
        filters_summary="Last closed fiscal period; entities you can see; USD at ERP average rates; intercompany excluded"),
    "Budget versus actual operating expenses by department for this fiscal year to date": _plan(
        title="Budget vs actual operating expenses by department, fiscal year to date", domain="finance",
        sql=f"""WITH actual AS (
  SELECT department, ROUND(SUM(amount_usd), 2) AS actual_usd FROM gl_monthly
  WHERE report_line = 'operating_expenses' AND fiscal_year = {CURRENT_FY}
    AND fiscal_year * 100 + fiscal_period <= {LAST_CLOSED}
  GROUP BY department),
budget AS (
  SELECT department, ROUND(SUM(amount_usd), 2) AS budget_usd FROM budget_monthly
  WHERE report_line = 'operating_expenses' AND fiscal_year = {CURRENT_FY}
    AND fiscal_year * 100 + fiscal_period <= {LAST_CLOSED}
  GROUP BY department)
SELECT a.department, a.actual_usd, b.budget_usd, ROUND(a.actual_usd - b.budget_usd, 2) AS variance_usd
FROM actual a JOIN budget b ON b.department = a.department ORDER BY a.actual_usd DESC""",
        chart_type="bar", x="department", y=["actual_usd", "budget_usd"], measures=["actual_usd", "budget_usd"],
        label_column="department", variance_pair=["actual_usd", "budget_usd"],
        filters_summary="Current fiscal year, closed periods only; entities you can see; USD"),
    "Monthly revenue in USD for the last 12 closed periods as a line chart": _plan(
        title="Monthly revenue, last 12 closed periods", domain="finance",
        sql=f"""SELECT * FROM (
  SELECT printf('FY%d-P%02d', fiscal_year, fiscal_period) AS fiscal_period_label,
         ROUND(SUM(amount_usd), 2) AS revenue_usd
  FROM gl_monthly
  WHERE report_line = 'revenue' AND fiscal_year * 100 + fiscal_period <= {LAST_CLOSED}
  GROUP BY fiscal_year, fiscal_period ORDER BY fiscal_year DESC, fiscal_period DESC LIMIT 12)
ORDER BY fiscal_period_label""",
        chart_type="line", x="fiscal_period_label", y=["revenue_usd"], measures=["revenue_usd"],
        label_column="fiscal_period_label", period_column="fiscal_period_label",
        filters_summary="Last 12 closed fiscal periods; entities you can see; USD at ERP average rates"),
}


def _n(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


class DemoPlanner:
    def plan(self, context: str, request: str) -> Plan:
        asked = request.split("New request: ", 1)[-1].split("\n", 1)[0].strip()
        if asked in DEMO_PLANS:
            return DEMO_PLANS[asked]
        return _plan(action="clarify", sql="", title="", chart_type="table", x="", y=[], measures=[], filters_summary="",
                     message="Demo mode answers the standard reports in the sidebar. Set ANTHROPIC_API_KEY "
                             "(and unset RA_DEMO_MODE) to ask anything in plain English.")

    def narrate(self, payload: dict) -> str:
        stats = payload["statistics"]["measures"]
        if not stats:
            return f"{payload['row_count']} rows returned."
        first = stats[0]
        name = first["measure"].replace("_usd", " (USD)").replace("_", " ")
        text = f"Total {name} is {_n(first['total'])} across {payload['row_count']} rows"
        if first.get("maximum_at"):
            text += f", highest for {first['maximum_at']} ({_n(first['maximum'])})"
        text += "."
        if first.get("pct_change") is not None:
            text += f" {first['latest_period']} was {first['pct_change']:+.1f}% versus {first['previous_period']}."
        variance = payload["statistics"].get("variance")
        if variance and variance.get("pct_variance") is not None:
            text += f" Actual is {variance['pct_variance']:+.1f}% against budget."
        if payload.get("provisional"):
            text += " Figures include an open period and may change."
        return text + " (Demo mode: summary generated without the language model.)"
