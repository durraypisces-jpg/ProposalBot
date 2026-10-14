"""Command line: set up the sample data, refresh the reporting store, or ask a
question without the web portal.

    python -m reporting_assistant.cli setup     # choose the AI and save its key (once)
    python -m reporting_assistant.cli check     # test the AI connection in plain English
    python -m reporting_assistant.cli init
    python -m reporting_assistant.cli refresh
    python -m reporting_assistant.cli ask --user omar "Stock value by site"
"""

from __future__ import annotations

import argparse
import json
import os
import uuid

from .assistant import Assistant
from .auth import demo_user
from .config import get_settings
from .planners import make_planner
from .query_service import QueryService
from .refresh import refresh_reporting_store
from .sample_data import build_sample_erp
from .semantic import load_semantic_layer
from .store import AppStore


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reporting_assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Choose the AI, enter its key once, and find a model that works")
    sub.add_parser("check", help="Test the AI connection and explain any problem")
    sub.add_parser("init", help="Create the sample ERP database and the reporting store")
    sub.add_parser("refresh", help="Refresh the reporting store from the ERP (run on a schedule)")
    ask = sub.add_parser("ask", help="Ask for a report")
    ask.add_argument("--user", default="admin")
    ask.add_argument("request")
    args = parser.parse_args(argv)

    if args.command == "setup":
        from .setup_wizard import run_setup
        raise SystemExit(0 if run_setup() else 1)

    settings = get_settings()
    if args.command == "check":
        raise SystemExit(0 if check(settings) else 1)
    if args.command == "init":
        build_sample_erp(settings.erp_db)
        print(json.dumps(refresh_reporting_store(settings.erp_db, settings.reporting_db), indent=2))
    elif args.command == "refresh":
        print(json.dumps(refresh_reporting_store(settings.erp_db, settings.reporting_db), indent=2))
    else:
        layer = load_semantic_layer(settings.semantic_layer_path)
        assistant = Assistant(
            layer=layer,
            queries=QueryService(settings.reporting_db, layer, settings.query_timeout_seconds, settings.max_rows),
            planner=make_planner(settings),
            store=AppStore(settings.app_db),
            reporting_db=settings.reporting_db,
        )
        reply = assistant.handle(demo_user(layer, args.user), uuid.uuid4().hex, args.request)
        if reply.kind != "report":
            print(f"[{reply.kind}] {reply.message}")
            return
        report = reply.report
        print(report["title"], "\n")
        print(report["narrative"], "\n")
        print(report["sql"], "\n")
        print(json.dumps(report["statistics"], indent=2))


def check(settings, say=print) -> bool:
    """Plain-English answer to "is the AI connection working?"."""
    provider = "demo" if settings.demo_mode else settings.provider
    say(f"Provider: {provider}" + (f", model: {settings.llm_model or '(not set)'}" if provider not in ("claude", "demo") else ""))
    if provider == "demo":
        say("Demo mode needs no AI. Unset RA_DEMO_MODE to use a real model.")
        return True
    if provider == "claude":
        try:
            make_planner(settings).narrate({"title": "connection check"})
        except Exception as exc:
            say(f"Not working: {exc}")
            return False
        say("Working: Claude answered.")
        return True

    from .openai_compatible import PROVIDERS, probe
    spec = PROVIDERS.get(provider)
    if spec is None:
        say(f"Unknown provider {provider!r}. Run: python -m reporting_assistant.cli setup")
        return False
    key = os.getenv(spec.key_env, "") if spec.key_env else ""
    if spec.key_env and not key:
        say(f"No key: {spec.key_env} is not set. Run: python -m reporting_assistant.cli setup")
        return False
    if not settings.llm_model:
        say("No model chosen: RA_LLM_MODEL is not set. Run: python -m reporting_assistant.cli setup")
        return False
    ok, message = probe(provider, settings.llm_model, key)
    say(("Working: " if ok else "Not working: ") + message)
    if not ok:
        say("To pick a model that works right now, run: python -m reporting_assistant.cli setup")
    return ok


if __name__ == "__main__":
    main()
