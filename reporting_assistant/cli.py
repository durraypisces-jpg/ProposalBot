"""Command line: set up the sample data, refresh the reporting store, or ask a
question without the web portal.

    python -m reporting_assistant.cli init
    python -m reporting_assistant.cli refresh
    python -m reporting_assistant.cli ask --user omar "Stock value by site"
"""

from __future__ import annotations

import argparse
import json
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
    sub.add_parser("init", help="Create the sample ERP database and the reporting store")
    sub.add_parser("refresh", help="Refresh the reporting store from the ERP (run on a schedule)")
    ask = sub.add_parser("ask", help="Ask for a report")
    ask.add_argument("--user", default="admin")
    ask.add_argument("request")
    args = parser.parse_args(argv)

    settings = get_settings()
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


if __name__ == "__main__":
    main()
