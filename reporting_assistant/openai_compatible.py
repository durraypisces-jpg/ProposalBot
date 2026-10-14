"""Planner for providers with an OpenAI-compatible chat completions API:
Google Gemini, Groq, OpenRouter and xAI Grok. It does the same two jobs as the
Claude planner, with the same instructions and plan schema.

    RA_PROVIDER=gemini   GEMINI_API_KEY=...      (free key: aistudio.google.com)
    RA_PROVIDER=groq     GROQ_API_KEY=...        (free key: console.groq.com)
    RA_PROVIDER=openrouter OPENROUTER_API_KEY=... (free models: openrouter.ai)
    RA_PROVIDER=grok     XAI_API_KEY=...         (console.x.ai)
    RA_PROVIDER=ollama   no key: a free model running on this machine (ollama.com)

plus RA_LLM_MODEL, a model name from that provider's model list.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

import httpx

from .llm import NARRATIVE_INSTRUCTIONS, PLAN_SCHEMA, PLANNER_INSTRUCTIONS, LLMRefused, Plan


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str | None  # None: no key needed
    console: str
    timeout: float = 120.0


PROVIDERS = {
    "gemini": Provider("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "aistudio.google.com"),
    "groq": Provider("Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "console.groq.com"),
    "openrouter": Provider("OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openrouter.ai"),
    "grok": Provider("xAI Grok", "https://api.x.ai/v1", "XAI_API_KEY", "console.x.ai"),
    # Local models answer slowly on a CPU, so allow up to 10 minutes per call.
    "ollama": Provider("Ollama", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"), None,
                       "ollama.com (run `ollama list` to see downloaded models)", timeout=600.0),
}


class ProviderError(RuntimeError):
    pass


RETRY_STATUSES = {429, 500, 502, 503, 504}  # rate limited, or the service is busy
RETRY_DELAYS = (2.0, 5.0, 10.0, 20.0)       # seconds; free tiers are often briefly overloaded


def _version_key(name: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def list_models(provider: str, api_key: str = "", client: httpx.Client | None = None) -> list[str]:
    """Model names this key can use, newest versions first."""
    spec = PROVIDERS[provider]
    client = client or httpx.Client(base_url=spec.base_url, timeout=30)
    response = client.get("/models", headers={"Authorization": f"Bearer {api_key or 'none'}"})
    if response.status_code >= 400:
        raise ProviderError(f"{spec.name} API error {response.status_code}: {response.text[:300]}")
    names = (m["id"].removeprefix("models/") for m in response.json().get("data", []))
    return sorted(names, key=_version_key, reverse=True)


PLAIN_ADVICE = {
    400: "The request was rejected. The model may not support chat; try another model.",
    401: "The API key is wrong. Copy it again from {console}.",
    403: "Access denied. The key may lack permission, or the service is restricted for your account or region.",
    404: "That model name doesn't exist or is retired for your account. Pick another model.",
    429: "Free quota or rate limit reached. Wait a minute, or try another model.",
    503: "The model is busy (high demand). Try again later, or pick another model.",
}


def probe(provider: str, model: str, api_key: str = "", client: httpx.Client | None = None) -> tuple[bool, str]:
    """One tiny request, no retries. Returns (worked, plain-English explanation)."""
    spec = PROVIDERS[provider]
    client = client or httpx.Client(base_url=spec.base_url, timeout=spec.timeout)
    try:
        response = client.post("/chat/completions", headers={"Authorization": f"Bearer {api_key or 'none'}"},
                               json={"model": model, "messages": [{"role": "user", "content": "Reply with: OK"}]})
    except httpx.HTTPError as exc:
        return False, f"Couldn't reach {spec.name}: {exc}"
    if response.status_code < 400:
        return True, f"{spec.name} model {model} answered."
    advice = PLAIN_ADVICE.get(response.status_code, "Unexpected error.").format(console=spec.console)
    return False, f"{response.status_code}: {advice}"


def _strip_fences(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return (match.group(1) if match else text).strip()


class OpenAICompatiblePlanner:
    def __init__(self, provider: str, model: str, api_key: str | None = None,
                 client: httpx.Client | None = None):
        if provider not in PROVIDERS:
            raise ProviderError(f"Unknown provider {provider!r}.")
        self.provider = PROVIDERS[provider]
        self.provider_id = provider
        if self.provider.key_env:
            api_key = api_key or os.getenv(self.provider.key_env, "")
            if not api_key:
                raise ProviderError(f"Set {self.provider.key_env} to an API key from {self.provider.console}.")
        self.client = client or httpx.Client(base_url=self.provider.base_url, timeout=self.provider.timeout)
        self.headers = {"Authorization": f"Bearer {api_key or 'none'}"}
        if not model:
            raise ProviderError(f"Set RA_LLM_MODEL to a {self.provider.name} model name.{self._model_hint()}")
        self.model = model
        self.json_schema_supported = True

    def _model_hint(self) -> str:
        """Best effort: list a few model names the key can use."""
        try:
            # Newest versions first ("gemini-3.8-flash" before "gemini-2.5-flash").
            names = list_models(self.provider_id, self.headers["Authorization"].removeprefix("Bearer "), self.client)
        except Exception:
            return f" See {self.provider.console} for the list."
        return f" Available: {', '.join(names[:15])}" if names else ""

    def _chat(self, system: str, user: str, response_format: dict | None = None) -> httpx.Response:
        body: dict = {"model": self.model,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if response_format:
            body["response_format"] = response_format
        for delay in (*RETRY_DELAYS, None):
            response = self.client.post("/chat/completions", json=body, headers=self.headers)
            if response.status_code not in RETRY_STATUSES or delay is None:
                return response
            retry_after = response.headers.get("retry-after", "")
            time.sleep(min(float(retry_after), 60.0) if retry_after.replace(".", "", 1).isdigit() else delay)
        return response

    def _text(self, response: httpx.Response) -> str:
        if response.status_code >= 400:
            raise self._error(response)
        message = response.json()["choices"][0]["message"]
        if message.get("refusal"):
            raise LLMRefused(message["refusal"])
        return message.get("content") or ""

    def _error(self, response: httpx.Response) -> ProviderError:
        try:
            payload = response.json()
            if isinstance(payload, list):
                payload = payload[0] if payload else {}
            detail = payload.get("error") or response.text
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail)
        except ValueError:
            detail = response.text
        return ProviderError(f"{self.provider.name} API error {response.status_code}: {detail}")

    def plan(self, context: str, request: str) -> Plan:
        system = PLANNER_INSTRUCTIONS + "\n\n" + context
        if self.json_schema_supported:
            response = self._chat(system, request, {
                "type": "json_schema", "json_schema": {"name": "report_plan", "schema": PLAN_SCHEMA, "strict": True}})
            if response.status_code != 400:
                return Plan.model_validate_json(_strip_fences(self._text(response)))
            # This model doesn't accept JSON schemas; use JSON mode from now on.
            self.json_schema_supported = False
        system += ("\n\nReply with ONE JSON object and nothing else. It must match this JSON Schema:\n"
                   + json.dumps(PLAN_SCHEMA))
        response = self._chat(system, request, {"type": "json_object"})
        if response.status_code == 400:
            response = self._chat(system, request)
        return Plan.model_validate_json(_strip_fences(self._text(response)))

    def narrate(self, payload: dict) -> str:
        return self._text(self._chat(NARRATIVE_INSTRUCTIONS, json.dumps(payload, indent=1, default=str))).strip()
