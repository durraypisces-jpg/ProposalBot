import pytest

from reporting_assistant.assistant import Assistant
from reporting_assistant.auth import demo_user
from reporting_assistant.llm import LLMRefused
from reporting_assistant.query_service import QueryService
from reporting_assistant.store import AppStore

from .conftest import TODAY, FakePlanner, make_plan


@pytest.fixture
def build(settings, layer):
    def _build(planner):
        store = AppStore(settings.app_db)
        assistant = Assistant(layer, QueryService(settings.reporting_db, layer), planner, store,
                              settings.reporting_db, today=TODAY)
        return assistant, store
    return _build


def test_report_flow(build, layer):
    planner = FakePlanner()
    assistant, store = build(planner)
    reply = assistant.handle(demo_user(layer, "omar"), "s1", "Stock value by site")

    assert reply.kind == "report"
    report = reply.report
    assert report["columns"] == ["site_id", "stock_value_usd"]
    assert report["row_count"] == 3
    assert report["narrative"] == planner.narrative
    assert report["statistics"]["measures"][0]["total"] == pytest.approx(sum(r[1] for r in report["rows"]), abs=0.01)
    # FR-10: source, filters and data-as-of on every report
    assert report["source"]["views"] == ["products", "stock_levels"]
    assert set(report["source"]["data_as_of"]) == {"inventory"}
    assert report["provisional"] is False
    # The narrative call receives calculated statistics, not raw SQL
    assert "statistics" in planner.narrate_calls[0]
    assert store.audit_entries()[0]["outcome"] == "report"
    assert store.get_report(report["id"])["title"] == report["title"]


def test_planner_only_sees_permitted_views(build, layer):
    planner = FakePlanner()
    assistant, _ = build(planner)
    assistant.handle(demo_user(layer, "sara"), "s1", "anything")
    context, request = planner.plan_calls[0]
    visible = context.split("Views that exist but this user may NOT see:")[0]
    assert "\ngl_monthly" in visible and "\nstock_levels" not in visible
    assert "Entities: PK01" in context
    assert "fiscal year FY2027, fiscal period 4" in request


def test_row_level_security_applies_to_reports(build, layer):
    assistant, _ = build(FakePlanner())
    report = assistant.handle(demo_user(layer, "amina"), "s1", "Stock value by site").report
    assert [r[0] for r in report["rows"]] == ["WH-NORTH"]


def test_clarifying_question(build, layer):
    planner = FakePlanner([make_plan(action="clarify", message="Which site?", sql="")])
    assistant, store = build(planner)
    reply = assistant.handle(demo_user(layer, "omar"), "s1", "stock please")
    assert (reply.kind, reply.message) == ("clarify", "Which site?")
    assert store.audit_entries()[0]["outcome"] == "clarify"


def test_restricted_request_is_declined_politely(build, layer):
    planner = FakePlanner([make_plan(sql="SELECT * FROM gl_monthly")])
    assistant, store = build(planner)
    reply = assistant.handle(demo_user(layer, "omar"), "s1", "show me revenue")
    assert reply.kind == "decline" and "access" in reply.message
    assert store.audit_entries()[0]["outcome"] == "rejected"


def test_failed_sql_is_repaired_once(build, layer):
    planner = FakePlanner([make_plan(sql="SELECT bad_column FROM products"), make_plan()])
    assistant, _ = build(planner)
    reply = assistant.handle(demo_user(layer, "omar"), "s1", "Stock value by site")
    assert reply.kind == "report"
    assert "no such column" in planner.plan_calls[1][1]


def test_gives_up_after_second_failure(build, layer):
    planner = FakePlanner([make_plan(sql="SELECT bad FROM products")])
    assistant, store = build(planner)
    reply = assistant.handle(demo_user(layer, "omar"), "s1", "Stock value by site")
    assert reply.kind == "error"
    assert store.audit_entries()[0]["outcome"] == "error"


def test_follow_up_sees_previous_sql(build, layer):
    planner = FakePlanner()
    assistant, _ = build(planner)
    omar = demo_user(layer, "omar")
    assistant.handle(omar, "s1", "Stock value by site")
    assistant.handle(omar, "s1", "now break it down by month")
    follow_up = planner.plan_calls[1][1]
    assert "Earlier in this conversation" in follow_up and "stock_levels" in follow_up
    assert "New request: now break it down by month" in follow_up


def test_open_period_marks_finance_report_provisional(build, layer):
    sql = ("SELECT report_line, ROUND(SUM(amount_usd), 2) AS amount_usd FROM gl_monthly "
           "WHERE fiscal_year = 2027 AND fiscal_period = {p} GROUP BY report_line")
    closed = make_plan(domain="finance", sql=sql.format(p=3), x="report_line", y=["amount_usd"],
                       measures=["amount_usd"], date_start="2026-09-01", date_end="2026-09-30")
    open_ = make_plan(domain="finance", sql=sql.format(p=4), x="report_line", y=["amount_usd"],
                      measures=["amount_usd"], date_start="2026-10-01", date_end="2026-10-31")
    assistant, _ = build(FakePlanner([closed, open_]))
    sara = demo_user(layer, "sara")
    assert assistant.handle(sara, "s1", "P&L September").report["provisional"] is False
    assert assistant.handle(sara, "s1", "P&L October").report["provisional"] is True


def test_refusal_is_declined(build, layer):
    class Refusing(FakePlanner):
        def plan(self, context, request):
            raise LLMRefused("no")
    assistant, store = build(Refusing())
    assert assistant.handle(demo_user(layer, "omar"), "s1", "x").kind == "decline"
    assert store.audit_entries()[0]["outcome"] == "refused"


def test_narrative_failure_still_returns_report(build, layer):
    class NoNarrative(FakePlanner):
        def narrate(self, payload):
            raise RuntimeError("API down")
    assistant, _ = build(NoNarrative())
    reply = assistant.handle(demo_user(layer, "omar"), "s1", "Stock value by site")
    assert reply.kind == "report" and reply.report["narrative"] == ""


def test_answer_to_clarifying_question_keeps_context(build, layer):
    planner = FakePlanner([make_plan(action="clarify", message="Which site?", sql=""), make_plan()])
    assistant, _ = build(planner)
    omar = demo_user(layer, "omar")
    assistant.handle(omar, "s1", "stock please")
    assert assistant.handle(omar, "s1", "WH-NORTH").kind == "report"
    second = planner.plan_calls[1][1]
    assert "Request: stock please" in second and "You replied: Which site?" in second
