from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from reporting_assistant.config import PACKAGE_DIR, Settings
from reporting_assistant.llm import Plan
from reporting_assistant.refresh import refresh_reporting_store
from reporting_assistant.sample_data import build_sample_erp
from reporting_assistant.semantic import load_semantic_layer

TODAY = date(2026, 10, 13)  # fiscal FY2027, period 4 (October) is open


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("data")
    build_sample_erp(path / "erp.db", today=TODAY)
    refresh_reporting_store(path / "erp.db", path / "reporting.db")
    return path


@pytest.fixture
def settings(data_dir, tmp_path) -> Settings:
    # Shared ERP and reporting store; a fresh app database per test.
    for name in ("erp.db", "reporting.db"):
        (tmp_path / name).symlink_to(data_dir / name)
    return Settings(data_dir=tmp_path)


@pytest.fixture(scope="session")
def layer():
    return load_semantic_layer(PACKAGE_DIR / "semantic_layer.yaml")


def make_plan(**overrides) -> Plan:
    fields = dict(
        action="query", message="", title="Stock value by site", domain="inventory",
        sql=("SELECT l.site_id, ROUND(SUM(l.qty_on_hand * p.unit_cost), 2) AS stock_value_usd "
             "FROM stock_levels l JOIN products p ON p.sku = l.sku "
             "WHERE l.snapshot_date = (SELECT MAX(snapshot_date) FROM stock_levels) "
             "GROUP BY l.site_id ORDER BY stock_value_usd DESC"),
        chart_type="bar", x="site_id", y=["stock_value_usd"], series="", measures=["stock_value_usd"],
        label_column="site_id", period_column="", variance_pair=[], date_start="", date_end="",
        filters_summary="Latest snapshot; all permitted sites; USD",
    )
    fields.update(overrides)
    return Plan(**fields)


class FakePlanner:
    """Stands in for Claude: returns scripted plans and records what it was sent."""

    def __init__(self, plans: list[Plan] | None = None, narrative: str = "Stock is highest at the central warehouse."):
        self.plans = list(plans or [make_plan()])
        self.narrative = narrative
        self.plan_calls: list[tuple[str, str]] = []
        self.narrate_calls: list[dict] = []

    def plan(self, context: str, request: str) -> Plan:
        self.plan_calls.append((context, request))
        return self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]

    def narrate(self, payload: dict) -> str:
        self.narrate_calls.append(payload)
        return self.narrative
