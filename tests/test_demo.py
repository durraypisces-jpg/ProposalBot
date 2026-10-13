import pytest

from reporting_assistant.assistant import Assistant
from reporting_assistant.auth import demo_user
from reporting_assistant.demo import DEMO_PLANS, DemoPlanner
from reporting_assistant.query_service import QueryService
from reporting_assistant.store import AppStore

from .conftest import TODAY


@pytest.fixture
def assistant(settings, layer):
    return Assistant(layer, QueryService(settings.reporting_db, layer), DemoPlanner(), AppStore(settings.app_db),
                     settings.reporting_db, today=TODAY)


def test_every_template_has_a_demo_plan(layer):
    assert {t["prompt"] for t in layer.templates} == set(DEMO_PLANS)


@pytest.mark.parametrize("template", ["stock-on-hand", "reorder-alerts", "slow-moving", "monthly-movements",
                                      "pnl-summary", "budget-vs-actual", "revenue-trend"])
def test_demo_templates_produce_reports(assistant, layer, template):
    t = next(t for t in layer.templates if t["id"] == template)
    for username in ("david", "omar" if t["domain"] == "inventory" else "sara"):
        reply = assistant.handle(demo_user(layer, username), "s", t["prompt"])
        assert reply.kind == "report", reply.message
        assert reply.report["row_count"] > 0 and reply.report["narrative"]


def test_free_form_requests_explain_demo_mode(assistant, layer):
    reply = assistant.handle(demo_user(layer, "omar"), "s", "anything else")
    assert reply.kind == "clarify" and "ANTHROPIC_API_KEY" in reply.message
