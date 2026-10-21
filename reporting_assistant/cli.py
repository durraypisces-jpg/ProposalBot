"""Command line: set up the sample data, refresh the reporting store, or ask a
question without the web portal.

    python -m reporting_assistant.cli setup     # choose the AI and save its key (once)
    python -m reporting_assistant.cli check     # test the AI connection in plain English
    python -m reporting_assistant.cli init
    python -m reporting_assistant.cli refresh
    python -m reporting_assistant.cli ask --user omar "Stock value by site"
    python -m reporting_assistant.cli usage                 # this month's token usage per client
    python -m reporting_assistant.cli flags                 # flagged results as benchmark stubs
    python -m reporting_assistant.cli sample-document --type purchase_order --out po.pdf
    python -m reporting_assistant.cli process-document --user omar --type purchase_order po.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

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
    usage = sub.add_parser("usage", help="Token usage this month (proposal: Token Metering)")
    usage.add_argument("--month", default=None, help="YYYY-MM (default: this month)")
    sub.add_parser("flags", help="Print open flagged results as stubs for eval/questions.yaml")
    sample = sub.add_parser("sample-document", help="Write a sample PO, purchase request or item-creation PDF")
    sample.add_argument("--type", default="purchase_order", choices=["purchase_order", "purchase_request", "item_creation"])
    sample.add_argument("--number", default="PO-2026-0412")
    sample.add_argument("--mistake", action="store_true", help="Make the order total wrong (goes to review)")
    sample.add_argument("--out", required=True)
    process = sub.add_parser("process-document", help="Capture a document: extract, check, create a draft")
    process.add_argument("--user", default="omar")
    process.add_argument("--type", default="purchase_order", choices=["purchase_order", "purchase_request", "item_creation"])
    process.add_argument("path")
    args = parser.parse_args(argv)

    if args.command == "setup":
        from .setup_wizard import run_setup
        raise SystemExit(0 if run_setup() else 1)

    settings = get_settings()
    if args.command in ("sample-document", "process-document") and not settings.reporting_db.exists():
        print(f"No sample data in {settings.data_dir}. Run first: python -m reporting_assistant.cli init")
        raise SystemExit(1)
    if args.command == "check":
        raise SystemExit(0 if check(settings) else 1)
    if args.command == "init":
        build_sample_erp(settings.erp_db)
        print(json.dumps(refresh_reporting_store(settings.erp_db, settings.reporting_db), indent=2))
    elif args.command == "refresh":
        print(json.dumps(refresh_reporting_store(settings.erp_db, settings.reporting_db), indent=2))
    elif args.command == "usage":
        store = AppStore(settings.app_db)
        layer = load_semantic_layer(settings.semantic_layer_path)
        for client_id in layer.clients:
            print(json.dumps({"client_id": client_id, **store.monthly_usage(client_id, args.month)}))
    elif args.command == "flags":
        print_flags(AppStore(settings.app_db))
    elif args.command == "sample-document":
        from .sample_documents import sample_lines, sample_pdf
        lines = sample_lines(args.type, settings.reporting_db, args.number, args.mistake)
        Path(args.out).write_bytes(sample_pdf(lines, args.type.replace("_", " ").title()))
        print(f"Wrote {args.out}")
    elif args.command == "process-document":
        from .app import make_extractor
        from .documents import DocumentService, LocalApprovalWorkflow, ReportingStoreMasterData
        layer = load_semantic_layer(settings.semantic_layer_path)
        service = DocumentService(AppStore(settings.app_db), make_extractor(settings),
                                  LocalApprovalWorkflow(settings.data_dir / "approval_workflow.db"),
                                  lambda _client: ReportingStoreMasterData(settings.reporting_db),
                                  settings.documents_dir)
        path = Path(args.path)
        document_id = service.upload(demo_user(layer, args.user), args.type, path.name, path.read_bytes())
        doc = service.process(document_id)
        print(json.dumps({k: doc[k] for k in ("id", "status", "read_method", "draft_id", "issues", "extracted")},
                         indent=2, default=str))
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


def print_flags(store: AppStore, say=print) -> None:
    """Flagged results go to the dev team and are added to the benchmark: each
    becomes a question stub; the data engineer fills in expected_sql."""
    flags = store.flags(status="open")
    if not flags:
        say("# No open flags.")
        return
    for flag in flags:
        say(f"- id: flagged-{flag['id']}\n  module: TODO\n  user: {flag['username']}\n"
            f"  question: {json.dumps(flag['request'])}\n  # flagged {flag['at']}: {flag['reason'] or 'no reason given'}\n"
            f"  # generated SQL: {' '.join(flag['sql'].split())}\n  expected_sql: >\n    TODO\n")


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
    key = os.getenv(spec.key_env or spec.optional_key_env or "", "") if (spec.key_env or spec.optional_key_env) else ""
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
