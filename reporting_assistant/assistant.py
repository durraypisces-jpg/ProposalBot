"""The request flow from proposal figure 1:

portal -> access control -> LLM plan -> query service -> analytics and charts
-> narrative -> report generator -> portal, with every step written to the
audit log.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .analytics import compute_statistics
from .auth import User
from .llm import LLMRefused, Plan, Planner
from .query_service import QueryFailed, QueryRejected, QueryService
from .refresh import last_refresh
from .semantic import SemanticLayer
from .store import AppStore

UI_ROW_LIMIT = 1000


@dataclass
class Reply:
    kind: str  # "report", "clarify", "decline" or "error"
    message: str = ""
    report: dict | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "report": self.report}


class Assistant:
    def __init__(self, layer: SemanticLayer, queries: QueryService, planner: Planner, store: AppStore,
                 reporting_db: Path, freshness_hours: dict[str, float] | None = None,
                 today: date | None = None):
        self.layer = layer
        self.queries = queries
        self.planner = planner
        self.store = store
        self.reporting_db = reporting_db
        self.freshness_hours = freshness_hours or {"inventory": 1.0, "finance": 24.0}
        self._today = today

    @property
    def today(self) -> date:
        return self._today or date.today()

    # Prompt context

    def _context(self, user: User) -> str:
        return self.layer.describe_for(user.role) + f"\n\nThe user: {user.name}. {user.describe_scope()}."

    def _request_text(self, user: User, session_id: str, request: str, error: str = "") -> str:
        fiscal_year, fiscal_period = self.layer.fiscal_period_of(self.today)
        lines = [
            f"Today is {self.today.isoformat()} (fiscal year FY{fiscal_year}, fiscal period {fiscal_period}).",
        ]
        history = self.store.history(session_id, user.username)
        if history:
            lines.append("\nEarlier in this conversation:")
            for i, turn in enumerate(history, 1):
                if turn["sql"]:
                    lines.append(f"{i}. Request: {turn['request']}\n   Report: {turn['title']}\n   SQL: {turn['sql']}")
                else:
                    lines.append(f"{i}. Request: {turn['request']}\n   You replied: {turn['title']}")
        lines.append(f"\nNew request: {request}")
        if error:
            lines.append(f"\nYour previous SQL for this request failed with: {error}\nWrite a corrected plan.")
        return "\n".join(lines)

    # Main entry point

    def handle(self, user: User, session_id: str, request: str) -> Reply:
        started = time.monotonic()
        audit = dict(username=user.username, role=user.role.name, session_id=session_id, request=request)

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        context = self._context(user)
        try:
            plan = self.planner.plan(context, self._request_text(user, session_id, request))
            if plan.action != "query":
                kind = "clarify" if plan.action == "clarify" else "decline"
                # Kept in the history so the user's answer to a question makes sense (FR-03).
                self.store.add_turn(session_id, user.username, request, plan.message, "")
                self.store.audit(**audit, outcome=kind, duration_ms=elapsed(), detail=plan.message)
                return Reply(kind, plan.message)

            try:
                result = self.queries.run(plan.sql, user)
            except QueryFailed as first_error:
                # One repair attempt with the database error fed back (risk: LLM misreads).
                plan = self.planner.plan(context, self._request_text(user, session_id, request, str(first_error)))
                if plan.action != "query":
                    self.store.audit(**audit, outcome=plan.action, duration_ms=elapsed(), detail=plan.message)
                    return Reply("clarify" if plan.action == "clarify" else "decline", plan.message)
                result = self.queries.run(plan.sql, user)
        except QueryRejected as exc:
            self.store.audit(**audit, outcome="rejected", duration_ms=elapsed(), detail=str(exc))
            return Reply("decline", f"Sorry, I can't run that report. {exc}")
        except QueryFailed as exc:
            self.store.audit(**audit, outcome="error", duration_ms=elapsed(), detail=str(exc))
            return Reply("error", "I couldn't build a working query for that request. Try rephrasing it, "
                                  "or name the period, site or entity you want.")
        except LLMRefused as exc:
            self.store.audit(**audit, outcome="refused", duration_ms=elapsed(), detail=str(exc))
            return Reply("decline", "Sorry, I can't help with that request.")
        except Exception as exc:  # the AI service failed (bad key, model name, network, outage)
            self.store.audit(**audit, outcome="error", duration_ms=elapsed(), detail=f"{type(exc).__name__}: {exc}")
            return Reply("error", f"The AI service couldn't answer: {exc}")

        report = self._build_report(user, request, plan, result)
        self.store.save_report(report)
        self.store.add_turn(session_id, user.username, request, plan.title, result.sql)
        self.store.audit(**audit, outcome="report", sql=result.sql, row_count=len(result.frame),
                         report_id=report["id"], duration_ms=elapsed())
        return Reply("report", report=report)

    # Report assembly

    def _build_report(self, user: User, request: str, plan: Plan, result) -> dict:
        frame = result.frame
        stats = compute_statistics(
            frame,
            measures=plan.measures or plan.y,
            label_column=plan.label_column or plan.x or None,
            period_column=plan.period_column or None,
            variance_pair=plan.variance_pair or None,
        )
        domains = self._domains(plan, result.views_used)
        data_as_of = self._data_as_of(domains)
        provisional = "finance" in domains and self._touches_open_period(plan)
        stale = self._stale_domains(data_as_of)

        payload = {
            "request": request,
            "title": plan.title,
            "filters": plan.filters_summary,
            "statistics": stats,
            "first_rows": json.loads(frame.head(25).to_json(orient="records")),
            "row_count": len(frame),
            "provisional": provisional,
            "truncated": result.truncated,
        }
        if frame.empty:
            narrative = "The query returned no rows for these filters."
        else:
            try:
                narrative = self.planner.narrate(payload)
            except Exception:  # the report is still useful without the narrative
                narrative = ""

        return {
            "id": uuid.uuid4().hex[:12],
            "username": user.username,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request": request,
            "title": plan.title,
            "narrative": narrative,
            "statistics": stats,
            "chart": {"type": plan.chart_type, "x": plan.x, "y": plan.y, "series": plan.series},
            "columns": list(frame.columns),
            "rows": json.loads(frame.head(UI_ROW_LIMIT).to_json(orient="split", index=False))["data"],
            "row_count": int(len(frame)),
            "truncated": result.truncated,
            # FR-10: source, filters, date range and data-as-of on every report.
            "source": {
                "views": result.views_used,
                "filters": plan.filters_summary,
                "date_start": plan.date_start,
                "date_end": plan.date_end,
                "data_as_of": data_as_of,
                "stale_domains": stale,
            },
            "provisional": provisional,
            "sql": result.sql,  # NFR "Transparency": finance users can check the logic
            "domain": plan.domain,
        }

    def _domains(self, plan: Plan, views_used: list[str]) -> set[str]:
        domains = {self.layer.views[v].domain for v in views_used if v in self.layer.views}
        domains.discard("shared")
        if not domains and plan.domain in ("inventory", "finance"):
            domains = {plan.domain}
        if plan.domain == "mixed":
            domains |= {"inventory", "finance"}
        return domains

    def _data_as_of(self, domains: set[str]) -> dict[str, str]:
        refreshed = last_refresh(self.reporting_db)
        return {d: refreshed[d] for d in sorted(domains) if d in refreshed}

    def _stale_domains(self, data_as_of: dict[str, str]) -> list[str]:
        now = datetime.now(timezone.utc)
        stale = []
        for domain, stamp in data_as_of.items():
            age_hours = (now - datetime.fromisoformat(stamp)).total_seconds() / 3600
            if age_hours > self.freshness_hours.get(domain, 24.0):
                stale.append(domain)
        return stale

    def _touches_open_period(self, plan: Plan) -> bool:
        """FR-18: figures from a period that is still open are provisional."""
        if not plan.date_start and not plan.date_end:
            return False
        start = plan.date_start or "0000-01-01"
        end = plan.date_end or self.today.isoformat()
        con = sqlite3.connect(f"file:{self.reporting_db}?mode=ro", uri=True)
        try:
            (count,) = con.execute(
                "SELECT COUNT(*) FROM src_fiscal_periods WHERE is_closed = 0 AND start_date <= ? AND end_date >= ? AND start_date <= ?",
                (end, start, self.today.isoformat()),
            ).fetchone()
        finally:
            con.close()
        return count > 0
