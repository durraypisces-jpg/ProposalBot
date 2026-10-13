"""Chooses the LLM behind the assistant (RA_PROVIDER): claude, grok or demo."""

from __future__ import annotations

from .config import Settings
from .llm import Planner

PROVIDERS = ("claude", "grok", "demo")


def make_planner(settings: Settings) -> Planner:
    provider = "demo" if settings.demo_mode else settings.provider
    if provider == "claude":
        from .llm import ClaudePlanner
        return ClaudePlanner(settings.model, settings.plan_effort, settings.narrative_effort)
    if provider == "grok":
        from .grok import GrokPlanner
        return GrokPlanner(settings.grok_model)
    if provider == "demo":
        from .demo import DemoPlanner
        return DemoPlanner()
    raise ValueError(f"RA_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}.")
