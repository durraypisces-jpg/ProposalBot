import json

import httpx
import pytest

from reporting_assistant.assistant import Assistant
from reporting_assistant.auth import demo_user
from reporting_assistant.config import Settings
from reporting_assistant.demo import DemoPlanner
from reporting_assistant.llm import ClaudePlanner, LLMRefused
from reporting_assistant.openai_compatible import PROVIDERS, OpenAICompatiblePlanner, ProviderError
from reporting_assistant.planners import make_planner
from reporting_assistant.query_service import QueryService
from reporting_assistant.store import AppStore

from .conftest import TODAY, make_plan


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Retries back off with time.sleep; tests don't need to wait."""
    sleeps = []
    monkeypatch.setattr("reporting_assistant.openai_compatible.time.sleep", sleeps.append)
    return sleeps


def fake_api(reply_for, base_url="https://api.example/v1"):
    """A stand-in provider: records each request, answers with reply_for(request, body)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request, body))
        return reply_for(request, body)

    return httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler)), seen


def message(content=None, refusal=None):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content, "refusal": refusal}}]})


def planner(client, provider="gemini", model="test-model"):
    return OpenAICompatiblePlanner(provider, model, api_key="test-key", client=client)


def test_plan_request_and_parsing():
    plan = make_plan()
    client, seen = fake_api(lambda req, body: message(plan.model_dump_json()))
    assert planner(client).plan("CONTEXT", "REQUEST") == plan

    request, body = seen[0]
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "test-model"
    assert body["messages"][0]["content"].endswith("CONTEXT")
    assert body["messages"][1] == {"role": "user", "content": "REQUEST"}
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True


def test_falls_back_to_json_mode_when_schemas_are_unsupported():
    plan = make_plan()

    def reply(req, body):
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema not supported"}})
        return message("```json\n" + plan.model_dump_json() + "\n```")

    client, seen = fake_api(reply)
    p = planner(client)
    assert p.plan("c", "r") == plan
    assert seen[1][1]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in seen[1][1]["messages"][0]["content"]
    p.plan("c", "r")  # later calls go straight to JSON mode
    assert len(seen) == 3 and seen[2][1]["response_format"]["type"] == "json_object"


def test_narrative_is_plain_text():
    client, seen = fake_api(lambda req, body: message("  Revenue rose 5%.  "))
    assert planner(client).narrate({"title": "T"}) == "Revenue rose 5%."
    assert "response_format" not in seen[0][1]


def test_refusal_and_api_errors():
    client, _ = fake_api(lambda req, body: message(refusal="no"))
    with pytest.raises(LLMRefused):
        planner(client).plan("c", "r")
    client, _ = fake_api(lambda req, body: httpx.Response(429, json=[{"error": {"message": "quota exceeded"}}]))
    with pytest.raises(ProviderError, match="Google Gemini API error 429: quota exceeded"):
        planner(client).plan("c", "r")


def test_missing_key_and_model_are_explained(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="GEMINI_API_KEY.*aistudio.google.com"):
        OpenAICompatiblePlanner("gemini", "m")
    models = {"data": [{"id": "models/model-2.5"}, {"id": "models/model-10"}, {"id": "models/model-3.8"}]}
    client, _ = fake_api(lambda req, body: httpx.Response(200, json=models))
    with pytest.raises(ProviderError, match="RA_LLM_MODEL.*Available: model-10, model-3.8, model-2.5"):
        planner(client, model="")


def test_drives_a_full_report(settings, layer):
    plan = make_plan()

    def reply(req, body):
        return message(plan.model_dump_json() if "response_format" in body else "Central holds the most stock.")

    client, _ = fake_api(reply)
    assistant = Assistant(layer, QueryService(settings.reporting_db, layer), planner(client),
                          AppStore(settings.app_db), settings.reporting_db, today=TODAY)
    report = assistant.handle(demo_user(layer, "omar"), "s", "Stock value by site").report
    assert report["row_count"] == 3 and report["narrative"] == "Central holds the most stock."


def test_service_errors_become_a_message(settings, layer):
    client, _ = fake_api(lambda req, body: httpx.Response(401, json={"error": "bad key"}))
    store = AppStore(settings.app_db)
    assistant = Assistant(layer, QueryService(settings.reporting_db, layer), planner(client), store,
                          settings.reporting_db)
    reply = assistant.handle(demo_user(layer, "omar"), "s", "anything")
    assert reply.kind == "error" and "401" in reply.message
    assert store.audit_entries()[0]["outcome"] == "error"


def test_provider_selection(monkeypatch, tmp_path):
    for provider in PROVIDERS.values():
        monkeypatch.setenv(provider.key_env, "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for name in ("gemini", "groq", "openrouter", "grok"):
        chosen = make_planner(Settings(data_dir=tmp_path, provider=name, llm_model="m"))
        assert isinstance(chosen, OpenAICompatiblePlanner) and chosen.provider is PROVIDERS[name]
    assert isinstance(make_planner(Settings(data_dir=tmp_path, provider="claude")), ClaudePlanner)
    assert isinstance(make_planner(Settings(data_dir=tmp_path, provider="gemini", demo_mode=True)), DemoPlanner)
    with pytest.raises(ValueError):
        make_planner(Settings(data_dir=tmp_path, provider="other"))


def test_busy_service_is_retried(no_waiting):
    replies = iter([httpx.Response(503, json={"error": {"message": "busy"}}),
                    httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow down"}}),
                    message("Done.")])
    client, seen = fake_api(lambda req, body: next(replies))
    assert planner(client).narrate({}) == "Done."
    assert len(seen) == 3 and no_waiting == [2.0, 3.0]


def test_gives_up_after_retries():
    client, seen = fake_api(lambda req, body: httpx.Response(503, json={"error": {"message": "busy"}}))
    with pytest.raises(ProviderError, match="503: busy"):
        planner(client).narrate({})
    assert len(seen) == 5
