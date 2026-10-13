"""Chooses the LLM behind the assistant (RA_PROVIDER): claude, gemini, groq,
openrouter, grok or demo."""

from __future__ import annotations

from .config import Settings
from .llm import Planner

PROVIDERS = ("claude", "gemini", "groq", "openrouter", "grok", "demo")


def make_planner(settings: Settings) -> Planner:
    provider = "demo" if settings.demo_mode else settings.provider
    if provider == "claude":
        from .llm import ClaudePlanner
        return ClaudePlanner(settings.model, settings.plan_effort, settings.narrative_effort)
    if provider in ("gemini", "groq", "openrouter", "grok"):
        from .openai_compatible import OpenAICompatiblePlanner
        return OpenAICompatiblePlanner(provider, settings.llm_model)
    if provider == "demo":
        from .demo import DemoPlanner
        return DemoPlanner()
    raise ValueError(f"RA_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}.")
