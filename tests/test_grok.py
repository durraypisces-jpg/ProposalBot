import json

import httpx
import pytest

from reporting_assistant.assistant import Assistant
from reporting_assistant.auth import demo_user
from reporting_assistant.config import Settings
from reporting_assistant.demo import DemoPlanner
from reporting_assistant.grok import GrokError, GrokPlanner
from reporting_assistant.llm import ClaudePlanner, LLMRefused
from reporting_assistant.planners import make_planner
from reporting_assistant.query_service import QueryService
from reporting_assistant.store import AppStore

from .conftest import TODAY, make_plan


def fake_xai(reply_for):
    """A stand-in xAI server: records each request, answers with reply_for(body)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request, body))
        return reply_for(body)

    client = httpx.Client(base_url="https://api.x.ai/v1", transport=httpx.MockTransport(handler))
    return client, seen


def message(content=None, refusal=None):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content, "refusal": refusal}}]})


def test_plan_request_and_parsing():
    plan = make_plan()
    client, seen = fake_xai(lambda body: message(plan.model_dump_json()))
    result = GrokPlanner("grok-test", api_key="xai-key", client=client).plan("CONTEXT", "REQUEST")

    assert result == plan
    request, body = seen[0]
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer xai-key"
    assert body["model"] == "grok-test"
    assert body["messages"][0]["role"] == "system" and body["messages"][0]["content"].endswith("CONTEXT")
    assert body["messages"][1] == {"role": "user", "content": "REQUEST"}
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert set(fmt["json_schema"]["schema"]["required"]) == set(plan.model_fields)


def test_narrative_is_plain_text():
    client, seen = fake_xai(lambda body: message("  Revenue rose 5%.  "))
    text = GrokPlanner("grok-test", api_key="k", client=client).narrate({"title": "T"})
    assert text == "Revenue rose 5%." and "response_format" not in seen[0][1]


def test_refusal_and_api_errors():
    client, _ = fake_xai(lambda body: message(refusal="no"))
    with pytest.raises(LLMRefused):
        GrokPlanner("grok-test", api_key="k", client=client).plan("c", "r")
    client, _ = fake_xai(lambda body: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(GrokError, match="404.*model not found"):
        GrokPlanner("grok-test", api_key="k", client=client).plan("c", "r")


def test_missing_settings_are_explained(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(GrokError, match="RA_GROK_MODEL"):
        GrokPlanner("", api_key="k")
    with pytest.raises(GrokError, match="XAI_API_KEY"):
        GrokPlanner("grok-test")


def test_grok_drives_a_full_report(settings, layer):
    plan = make_plan()

    def reply(body):
        return message(plan.model_dump_json() if "response_format" in body else "Central holds the most stock.")

    client, _ = fake_xai(reply)
    assistant = Assistant(layer, QueryService(settings.reporting_db, layer),
                          GrokPlanner("grok-test", api_key="k", client=client), AppStore(settings.app_db),
                          settings.reporting_db, today=TODAY)
    report = assistant.handle(demo_user(layer, "omar"), "s", "Stock value by site").report
    assert report["row_count"] == 3 and report["narrative"] == "Central holds the most stock."


def test_service_errors_become_a_message(settings, layer):
    client, _ = fake_xai(lambda body: httpx.Response(401, json={"error": "bad key"}))
    store = AppStore(settings.app_db)
    assistant = Assistant(layer, QueryService(settings.reporting_db, layer),
                          GrokPlanner("grok-test", api_key="k", client=client), store, settings.reporting_db)
    reply = assistant.handle(demo_user(layer, "omar"), "s", "anything")
    assert reply.kind == "error" and "401" in reply.message
    assert store.audit_entries()[0]["outcome"] == "error"


def test_provider_selection(monkeypatch, tmp_path):
    monkeypatch.setenv("XAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(make_planner(Settings(data_dir=tmp_path, provider="grok", grok_model="g")), GrokPlanner)
    assert isinstance(make_planner(Settings(data_dir=tmp_path, provider="claude")), ClaudePlanner)
    assert isinstance(make_planner(Settings(data_dir=tmp_path, provider="grok", demo_mode=True)), DemoPlanner)
    with pytest.raises(ValueError):
        make_planner(Settings(data_dir=tmp_path, provider="other"))
