import json

import httpx

from reporting_assistant.cli import check
from reporting_assistant.config import Settings, load_env_file
from reporting_assistant.openai_compatible import _version_key
from reporting_assistant.setup_wizard import chat_model_candidates, run_setup

MODELS = {"data": [{"id": f"models/{n}"} for n in (
    "gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-2.5-flash-image",
    "gemini-3.8-pro", "deep-research-preview-04-2026")]}


def fake_gemini(working: set[str], status: int = 503):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model in working:
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        return httpx.Response(status, json={"error": {"message": "nope"}})

    client = httpx.Client(base_url="https://gemini.test/v1", transport=httpx.MockTransport(handler))
    return client, calls


def scripted(*answers):
    answers = iter(answers)
    return lambda prompt: next(answers)


def test_candidates_put_lite_first_and_drop_non_chat_models():
    names = sorted(["gemini-3.8-pro", "gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-2.5-flash-image",
                    "deep-research-preview-04-2026", "gemini-2.5-flash-lite"], key=_version_key, reverse=True)
    assert chat_model_candidates(names) == ["gemini-3.8-flash-lite", "gemini-2.5-flash-lite",
                                            "gemini-3.8-flash", "gemini-3.8-pro"]


def test_setup_finds_a_working_model_and_saves_env(tmp_path):
    client, calls = fake_gemini(working={"gemini-3.8-flash"})
    said = []
    env = tmp_path / ".env"
    assert run_setup(ask=scripted("1"), ask_secret=scripted("my-key"), say=said.append, client=client, env_path=env)
    assert calls == ["gemini-3.8-flash-lite", "gemini-3.8-flash"]  # lite first, busy, then flash works
    saved = dict(line.split("=", 1) for line in env.read_text().splitlines())
    assert saved == {"RA_PROVIDER": "gemini", "RA_LLM_MODEL": "gemini-3.8-flash", "GEMINI_API_KEY": "my-key"}
    assert "my-key" not in "\n".join(said)  # the key is never printed


def test_setup_reports_when_nothing_answers(tmp_path):
    client, _ = fake_gemini(working=set())
    said = []
    env = tmp_path / ".env"
    assert not run_setup(ask=scripted("1"), ask_secret=scripted("k"), say=said.append, client=client, env_path=env)
    assert not env.exists() and any("busy" in line for line in said)


def test_setup_explains_how_to_start_ollama(tmp_path):
    def down(request):
        raise httpx.ConnectError("refused")
    client = httpx.Client(base_url="http://localhost:11434/v1", transport=httpx.MockTransport(down))
    said = []
    assert not run_setup(ask=scripted("2"), say=said.append, client=client, env_path=tmp_path / ".env")
    assert any("ollama serve" in line for line in said)


def test_env_file_is_loaded_without_overriding_the_shell(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("RA_TEST_A=from-file\nRA_TEST_B='quoted'\n# comment\n")
    monkeypatch.setenv("RA_TEST_A", "from-shell")
    monkeypatch.delenv("RA_TEST_B", raising=False)
    load_env_file(env)
    import os
    assert os.environ["RA_TEST_A"] == "from-shell" and os.environ["RA_TEST_B"] == "quoted"
    monkeypatch.delenv("RA_TEST_B")


def test_check_explains_missing_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    said = []
    assert not check(Settings(data_dir=tmp_path, provider="gemini", llm_model="m"), say=said.append)
    assert "GEMINI_API_KEY is not set" in said[-1]
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    said.clear()
    assert not check(Settings(data_dir=tmp_path, provider="gemini", llm_model=""), say=said.append)
    assert "RA_LLM_MODEL is not set" in said[-1]


def test_check_gives_plain_advice(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    client, _ = fake_gemini(working=set(), status=404)
    monkeypatch.setattr("reporting_assistant.openai_compatible.httpx.Client", lambda **kw: client)
    said = []
    assert not check(Settings(data_dir=tmp_path, provider="gemini", llm_model="old-model"), say=said.append)
    assert "retired" in said[1] and "cli setup" in said[2]
