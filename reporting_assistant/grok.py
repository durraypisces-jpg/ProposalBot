"""Grok (xAI) planner: the same two jobs as the Claude planner, through xAI's
OpenAI-compatible chat completions API.

Set XAI_API_KEY and RA_GROK_MODEL (copy the model name from the Models page of
console.x.ai), then RA_PROVIDER=grok.
"""

from __future__ import annotations

import json
import os

import httpx

from .llm import NARRATIVE_INSTRUCTIONS, PLAN_SCHEMA, PLANNER_INSTRUCTIONS, LLMRefused, Plan

XAI_BASE_URL = "https://api.x.ai/v1"


class GrokError(RuntimeError):
    pass


class GrokPlanner:
    def __init__(self, model: str, api_key: str | None = None, base_url: str = XAI_BASE_URL,
                 client: httpx.Client | None = None, timeout: float = 120.0):
        if not model:
            raise GrokError("Set RA_GROK_MODEL to a model name from the Models page of console.x.ai.")
        api_key = api_key or os.getenv("XAI_API_KEY", "")
        if not api_key:
            raise GrokError("Set XAI_API_KEY to an API key from console.x.ai.")
        self.model = model
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def _chat(self, system: str, user: str, schema: dict | None = None) -> str:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "report_plan", "schema": schema, "strict": True},
            }
        response = self.client.post("/chat/completions", json=body, headers=self.headers)
        if response.status_code >= 400:
            try:
                detail = response.json().get("error") or response.text
            except ValueError:
                detail = response.text
            raise GrokError(f"xAI API error {response.status_code}: {detail}")
        message = response.json()["choices"][0]["message"]
        if message.get("refusal"):
            raise LLMRefused(message["refusal"])
        return message.get("content") or ""

    def plan(self, context: str, request: str) -> Plan:
        text = self._chat(PLANNER_INSTRUCTIONS + "\n\n" + context, request, schema=PLAN_SCHEMA)
        return Plan.model_validate_json(text)

    def narrate(self, payload: dict) -> str:
        return self._chat(NARRATIVE_INSTRUCTIONS, json.dumps(payload, indent=1, default=str)).strip()
