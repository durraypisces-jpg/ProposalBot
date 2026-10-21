"""Chooses the LLM behind the assistant (RA_PROVIDER): vllm (our own GPU
server, the proposal's production setup), claude, gemini, groq, openrouter,
grok, ollama or demo."""

from __future__ import annotations

from .config import Settings
from .llm import Planner

PROVIDERS = ("vllm", "claude", "gemini", "groq", "openrouter", "grok", "ollama", "demo")
OPENAI_COMPATIBLE = ("vllm", "gemini", "groq", "openrouter", "grok", "ollama")


def make_planner(settings: Settings) -> Planner:
    provider = "demo" if settings.demo_mode else settings.provider
    if provider == "claude":
        from .llm import ClaudePlanner
        return ClaudePlanner(settings.model, settings.plan_effort, settings.narrative_effort,
                             sql_dialect=settings.sql_dialect)
    if provider in OPENAI_COMPATIBLE:
        from .openai_compatible import OpenAICompatiblePlanner
        return OpenAICompatiblePlanner(provider, settings.llm_model, summary_model=settings.summary_model,
                                       sql_dialect=settings.sql_dialect)
    if provider == "demo":
        from .demo import DemoPlanner
        return DemoPlanner()
    raise ValueError(f"RA_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}.")
