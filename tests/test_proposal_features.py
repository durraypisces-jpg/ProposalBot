"""Tests for the v5 proposal: token metering and quotas, confirm before running,
verified report library, two-step results, flags, client isolation, the SQL
validator, the SQL Server runner and the LLM token counts."""

import json

import pytest
from fastapi.testclient import TestClient

from reporting_assistant.app import create_app
from reporting_assistant.assistant import Assistant, request_key
from reporting_assistant.auth import UnknownUser, demo_user, user_from_claims
from reporting_assistant.llm import LLMRefused, Usage, UsageTracker, planner_instructions
from reporting_assistant.openai_compatible import PROVIDERS, OpenAICompatiblePlanner, _usage_from
from reporting_assistant.query_service import QueryRejected, QueryService
from reporting_assistant.sql_validator import SQLValidationError, validate_select
from reporting_assistant.sqlserver import ClientConnections, SqlServerQueryService
from reporting_assistant.store import AppStore

from .conftest import TODAY, FakePlanner, make_plan


class MeteredPlanner(FakePlanner, UsageTracker):
    """FakePlanner that reports token counts like a real model server."""

    def __init__(self, *args, **kwargs):
        FakePlanner.__init__(self, *args, **kwargs)
        UsageTracker.__init__(self)

    def plan(self, context, request):
        self._record_usage(Usage("arctic-text2sql-r1-7b", 3850, 1620, 1200))
        return super().plan(context, request)

    def narrate(self, payload):
        self._record_usage(Usage("qwen3.8-27b", 400, 80))
        return super().narrate(payload)


@pytest.fixture
def build(settings, layer):
    def _build(planner, **options):
        store = AppStore(settings.app_db)
        assistant = Assistant(layer, QueryService(settings.reporting_db, layer), planner, store,
                              settings.reporting_db, today=TODAY, **options)
        return assistant, store
    return _build


# Token metering

def test_every_request_gets_one_usage_row(build, layer):
    plans = [make_plan(), make_plan(action="clarify", message="Which site?", sql=""),
             make_plan(sql="SELECT * FROM gl_monthly"), make_plan(sql="SELECT nope FROM products")]
    planner = MeteredPlanner(plans)
    assistant, store = build(planner)
    omar = demo_user(layer, "omar")
    replies = [assistant.handle(omar, "s", f"request {i}") for i in range(4)]
    assert [r.kind for r in replies] == ["report", "clarify", "decline", "error"]
    rows = [store.usage_row(r.request_id) for r in replies]
    assert all(rows)
    assert [r["status"] for r in rows] == ["success", "clarify", "blocked", "failed"]
    assert [r["billable"] for r in rows] == [1, 0, 0, 0]  # failed or blocked requests are not charged
    # Report: plan + summary in the same row, with reasoning logged separately.
    assert rows[0]["input_tokens"] == 3850 + 400 and rows[0]["output_tokens"] == 1620 + 80
    assert rows[0]["reasoning_tokens"] == 1200 and rows[0]["llm_calls"] == 2
    assert rows[0]["client_id"] == "demo-client" and rows[0]["user_id"] == "omar"
    assert rows[0]["module"] == "inventory"
    assert "arctic" in rows[0]["model"] and "qwen" in rows[0]["model"]
    # The failed query was retried once: both plan calls are in its row.
    assert rows[3]["llm_calls"] == 2


def test_refusals_and_outages_are_logged(build, layer):
    class Refusing(MeteredPlanner):
        def plan(self, context, request):
            raise LLMRefused("no")

    assistant, store = build(Refusing())
    reply = assistant.handle(demo_user(layer, "omar"), "s", "x")
    assert reply.kind == "decline" and store.usage_row(reply.request_id)["status"] == "declined"


def test_quota_warns_at_80_percent_and_blocks_at_100(build, layer):
    planner = MeteredPlanner()
    assistant, store = build(planner, monthly_token_quota=20_000)
    omar = demo_user(layer, "omar")
    first = assistant.handle(omar, "s", "Stock value by site")  # 5,950 tokens
    assert first.kind == "report" and not first.notice
    assistant.handle(omar, "s", "Stock value by site")
    third = assistant.handle(omar, "s", "Stock value by site")  # used 11,900 + estimate: about 60%
    assert third.kind == "report"
    fourth = assistant.handle(omar, "s", "Stock value by site")  # used 17,850: over 80%
    assert fourth.kind == "report" and "%" in fourth.notice
    blocked = assistant.handle(omar, "s", "Stock value by site")  # used 23,800: over the limit
    assert blocked.kind == "blocked"
    assert store.usage_row(blocked.request_id)["status"] == "blocked"
    assert store.usage_row(blocked.request_id)["input_tokens"] == 0
    assert store.monthly_usage("demo-client")["billable_requests"] == 4


# Confirm before running

def test_new_request_is_restated_and_runs_only_after_confirmation(build, layer):
    planner = MeteredPlanner()
    assistant, store = build(planner, confirm_new_requests=True)
    omar = demo_user(layer, "omar")
    reply = assistant.handle(omar, "s1", "Stock value by site")
    assert reply.kind == "confirm" and reply.pending_id
    assert "Stock value by site" in reply.message and "Latest snapshot" in reply.message
    assert not planner.narrate_calls  # nothing ran yet
    assert all(entry["outcome"] != "report" for entry in store.audit_entries())
    assert store.usage_row(reply.request_id)["status"] == "awaiting_confirmation"

    # Only the same user can confirm, and only once.
    assert assistant.confirm(demo_user(layer, "david"), reply.pending_id).kind == "error"
    done = assistant.confirm(omar, reply.pending_id)
    assert done.kind == "report" and done.request_id == reply.request_id
    row = store.usage_row(reply.request_id)
    assert row["status"] == "success" and row["billable"] == 1 and row["llm_calls"] == 2
    assert assistant.confirm(omar, reply.pending_id).kind == "error"


def test_restricted_query_is_declined_without_asking_to_confirm(build, layer):
    assistant, _ = build(FakePlanner([make_plan(sql="SELECT * FROM gl_monthly")]), confirm_new_requests=True)
    reply = assistant.handle(demo_user(layer, "omar"), "s", "revenue")
    assert reply.kind == "decline" and not reply.pending_id


# Verified report library

def test_verified_report_is_reused_without_the_model(build, layer):
    planner = FakePlanner()
    assistant, store = build(planner, confirm_new_requests=True)
    omar = demo_user(layer, "omar")
    pending = assistant.handle(omar, "s", "Stock value by site")
    report = assistant.confirm(omar, pending.pending_id).report
    assistant.verify(report, verified_by="admin")

    calls = len(planner.plan_calls)
    again = assistant.handle(omar, "s2", "  stock VALUE by site? ")  # same question, different wording of case/punctuation
    assert again.kind == "report" and again.report["verified"] is True  # no confirmation step
    assert len(planner.plan_calls) == calls  # no model call to write SQL
    # Row-level security still applies to the verified query.
    amina = assistant.handle(demo_user(layer, "amina"), "s3", "Stock value by site")
    assert [r[0] for r in amina.report["rows"]] == ["WH-NORTH"]
    # A role without the module does not get it.
    sara_planner_calls = len(planner.plan_calls)
    assistant.handle(demo_user(layer, "sara"), "s4", "Stock value by site")
    assert len(planner.plan_calls) == sara_planner_calls + 1
    assert request_key("Stock value, by site!") == "stock value by site"


# Two-step results: table and chart first, then the summary

def test_summary_comes_second_and_sees_only_a_sample(build, layer):
    planner = MeteredPlanner()
    assistant, store = build(planner, defer_summary=True)
    reply = assistant.handle(demo_user(layer, "omar"), "s", "Stock value by site")
    report = reply.report
    assert report["narrative"] is None and report["summary_pending"] is True and not planner.narrate_calls
    assert store.usage_row(reply.request_id)["llm_calls"] == 1

    assert assistant.summarize(store.get_report(report["id"])) == planner.narrative
    payload = planner.narrate_calls[0]
    assert len(payload["first_rows"]) <= 10 and "statistics" in payload
    assert store.usage_row(reply.request_id)["llm_calls"] == 2  # added to the same usage row
    assert store.get_report(report["id"])["narrative"] == planner.narrative
    assistant.summarize(store.get_report(report["id"]))
    assert len(planner.narrate_calls) == 1  # not written twice


# SQL validator

@pytest.mark.parametrize("sql", [
    "DROP TABLE products", "SELECT 1; DELETE FROM products", "SELECT * FROM sqlite_master",
    "SELECT * FROM main.src_products", "SELECT * INTO t FROM products", "PRAGMA table_info(products)",
    "SELECT load_extension('x')", "SELECT * FROM (SELECT * FROM src_products)", "UPDATE products SET sku = 1",
    "WITH x AS (DELETE FROM products RETURNING *) SELECT * FROM x",
])
def test_validator_rejects(sql, layer):
    with pytest.raises(SQLValidationError):
        validate_select(sql, set(layer.views))


@pytest.mark.parametrize("sql", [
    "EXEC xp_cmdshell 'dir'", "SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'select 1')", "SELECT * FROM sys.objects",
    "SELECT name INTO #t FROM products", "SELECT * FROM dbo.products", "SELECT * FROM otherdb.rpt.products",
])
def test_validator_rejects_tsql(sql, layer):
    with pytest.raises(SQLValidationError):
        validate_select(sql, set(layer.views), "tsql", reporting_schema="rpt")


def test_validator_accepts_reporting_queries(layer):
    assert validate_select("WITH x AS (SELECT * FROM products) SELECT TOP 5 * FROM x", set(layer.views), "tsql") \
        == ["products"]
    assert validate_select("SELECT * FROM rpt.products", set(layer.views), "tsql", reporting_schema="rpt") \
        == ["products"]


def test_tsql_prompt():
    text = planner_instructions("tsql")
    assert "T-SQL" in text and "TOP n" in text and "SQLite" not in text
    assert "SQLite" in planner_instructions("sqlite")


# SQL Server runner (fake connection: no server needed)

class FakeCursor:
    def __init__(self, log, fail=None):
        self.log, self.fail, self.description = log, fail, None

    def execute(self, sql, *params):
        self.log.append((sql, params))
        if "sp_set_session_context" not in sql:
            if self.fail:
                raise self.fail
            self.description = [("site_id",), ("units",)]
        return self

    def fetchmany(self, n):
        return [("WH-NORTH", 10)]


class FakeConnection:
    def __init__(self, log, fail=None):
        self.log, self.fail, self.closed, self.timeout = log, fail, False, 0

    def cursor(self):
        return FakeCursor(self.log, self.fail)

    def close(self):
        self.closed = True


def test_sqlserver_sets_and_clears_the_users_role(layer):
    log, connections = [], []

    def connect(_conn_str):
        connections.append(FakeConnection(log))
        return connections[-1]

    service = SqlServerQueryService("DSN=x", layer, connect=connect)
    result = service.run("SELECT site_id, SUM(qty_on_hand) AS units FROM stock_levels GROUP BY site_id",
                         demo_user(layer, "amina"))
    assert result.frame.values.tolist() == [["WH-NORTH", 10]] and result.views_used == ["stock_levels"]
    set_calls = [p for sql, p in log if "sp_set_session_context" in sql and "NULL" not in sql]
    assert ("ra_sites", "WH-NORTH") in set_calls and ("ra_role", "warehouse_manager") in set_calls
    cleared = [p[0] for sql, p in log if "NULL" in sql]
    assert set(cleared) == {"ra_user", "ra_role", "ra_sites", "ra_entities"}
    assert connections[0].closed and connections[0].timeout == 30


def test_sqlserver_clears_role_even_when_the_query_fails(layer):
    log = []
    service = SqlServerQueryService("DSN=x", layer, connect=lambda _c: FakeConnection(
        log, Exception("[42000] The SELECT permission was denied on the object (229)")))
    with pytest.raises(QueryRejected):
        service.run("SELECT * FROM stock_levels", demo_user(layer, "omar"))
    assert sum("NULL" in sql for sql, _ in log) == 4


def test_sqlserver_rejects_before_connecting(layer):
    def connect(_c):
        raise AssertionError("must not connect")
    service = SqlServerQueryService("DSN=x", layer, connect=connect)
    with pytest.raises(QueryRejected):
        service.run("DROP TABLE stock_levels", demo_user(layer, "admin"))
    with pytest.raises(QueryRejected, match="access to gl_monthly"):
        service.run("SELECT * FROM gl_monthly", demo_user(layer, "omar"))


def test_client_connection_strings_come_from_settings():
    connections = ClientConnections({"RA_SQLSERVER_BETA_CLIENT": "Server=beta"})
    assert connections.connection_string("beta-client") == "Server=beta"
    with pytest.raises(LookupError):
        connections.connection_string("demo-client")


# Model server token counts

def test_vllm_usage_with_reasoning_breakdown():
    body = {"model": "arctic", "usage": {"prompt_tokens": 3850, "completion_tokens": 1620,
                                         "completion_tokens_details": {"reasoning_tokens": 1200}}}
    assert _usage_from(body, "m", {"content": "{}"}) == Usage("arctic", 3850, 1620, 1200)


def test_vllm_usage_counts_reasoning_text_when_not_broken_out():
    body = {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    usage = _usage_from(body, "qwen", {"content": "{}", "reasoning_content": "x" * 80})
    assert usage.input_tokens == 100 and usage.output_tokens == 50 and usage.reasoning_tokens == 20


def test_vllm_needs_no_key(monkeypatch):
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    planner = OpenAICompatiblePlanner("vllm", "arctic", summary_model="qwen")
    assert planner.provider is PROVIDERS["vllm"] and planner.summary_model == "qwen"
    monkeypatch.setenv("VLLM_API_KEY", "secret")
    assert OpenAICompatiblePlanner("vllm", "arctic").headers["Authorization"] == "Bearer secret"


# Web API: clients, flags, verification, summaries, usage

@pytest.fixture
def client(settings):
    return TestClient(create_app(settings, planner=MeteredPlanner()))


def login(client, username):
    token = client.post("/api/login", json={"username": username}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_each_client_has_its_own_database(client, settings):
    omar, bilal = login(client, "omar"), login(client, "bilal")
    mine = client.post("/api/chat", json={"message": "Stock value by site"}, headers=omar).json()["report"]
    theirs = client.post("/api/chat", json={"message": "Stock value by site"}, headers=bilal).json()["report"]
    assert mine["client_id"] == "demo-client" and theirs["client_id"] == "beta-client"
    assert mine["rows"] != theirs["rows"]  # different databases, different figures
    assert (settings.data_dir / "clients" / "beta-client" / "reporting.db").exists()
    # Reports, usage and audit entries never cross clients.
    assert client.get(f"/api/reports/{mine['id']}", headers=bilal).status_code == 404
    assert client.get("/api/usage", headers=bilal).json()["month"]["requests"] == 1
    admin = login(client, "admin")
    assert {e["client_id"] for e in client.get("/api/admin/audit", headers=admin).json()} == {"demo-client"}


def test_client_comes_from_the_login_not_the_chat(layer):
    with pytest.raises(UnknownUser):
        user_from_claims(layer, {"username": "x", "role": "ops_lead", "client_id": "client-55"})


def test_summary_flag_and_verify_endpoints(client):
    omar, admin = login(client, "omar"), login(client, "admin")
    report = client.post("/api/chat", json={"message": "Stock value by site"}, headers=omar).json()["report"]
    assert report["summary_pending"] is True
    assert client.get(f"/api/reports/{report['id']}/summary", headers=omar).json()["narrative"]

    assert client.post(f"/api/reports/{report['id']}/flag", json={"reason": "North looks too high"},
                       headers=omar).status_code == 200
    flags = client.get("/api/admin/flags", headers=admin).json()
    assert flags[0]["reason"] == "North looks too high" and flags[0]["sql"] == report["sql"]
    assert client.get("/api/admin/flags", headers=omar).status_code == 403

    assert client.post(f"/api/admin/verified/{report['id']}", headers=omar).status_code == 403
    admin_report = client.post("/api/chat", json={"message": "Stock value by site"}, headers=admin).json()["report"]
    assert client.post(f"/api/admin/verified/{admin_report['id']}", headers=admin).status_code == 200
    assert client.get("/api/admin/verified", headers=admin).json()[0]["request"] == "Stock value by site"
    usage = client.get("/api/admin/usage", headers=admin).json()
    assert usage and all(row["client_id"] == "demo-client" for row in usage)


def test_confirm_endpoint(settings):
    from dataclasses import replace
    client = TestClient(create_app(replace(settings, confirm_new_requests=True), planner=MeteredPlanner()))
    omar = login(client, "omar")
    reply = client.post("/api/chat", json={"message": "Stock value by site"}, headers=omar).json()
    assert reply["kind"] == "confirm"
    done = client.post("/api/chat/confirm", json={"pending_id": reply["pending_id"]}, headers=omar).json()
    assert done["kind"] == "report"
    again = client.post("/api/chat/confirm", json={"pending_id": reply["pending_id"]}, headers=omar).json()
    assert again["kind"] == "error"


def test_flags_become_benchmark_stubs(settings):
    from reporting_assistant.cli import print_flags
    store = AppStore(settings.app_db)
    store.add_flag(client_id="demo-client", username="omar", report_id="r1", request="Stock by site",
                   sql="SELECT 1", reason="wrong")
    lines = []
    print_flags(store, lines.append)
    assert "question: \"Stock by site\"" in lines[0] and "expected_sql" in lines[0]


def test_usage_row_merges_models(settings):
    store = AppStore(settings.app_db)
    store.log_usage(request_id="r", client_id="c", user_id="u", module="inventory", status="awaiting_confirmation",
                    usage=[Usage("a", 10, 5)])
    store.log_usage(request_id="r", client_id="c", user_id="u", module="none", status="success",
                    usage=[Usage("b", 1, 1)])
    row = store.usage_row("r")
    assert (row["input_tokens"], row["model"], row["module"], row["billable"]) == (11, "a,b", "inventory", 1)
    assert json.loads(json.dumps(store.monthly_usage("c")))["billable_tokens"] == 17
