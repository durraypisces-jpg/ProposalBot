"""The request flow from proposal figure 1:

portal -> access control -> LLM plan -> query service -> analytics and charts
-> narrative -> report generator -> portal, with every step written to the
audit log.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .analytics import compute_statistics
from .auth import User
from .llm import LLMRefused, Plan, Planner, take_usage
from .query_service import QueryFailed, QueryRejected, QueryService
from .refresh import last_refresh
from .semantic import SemanticLayer
from .store import AppStore
from .tokens import count_tokens

UI_ROW_LIMIT = 1000
SUMMARY_SAMPLE_ROWS = 10


@dataclass
class Reply:
    # "report", "clarify", "decline", "error", "confirm" (waiting for the user
    # to confirm the restated request) or "blocked" (token quota used up)
    kind: str
    message: str = ""
    report: dict | None = None
    request_id: str = ""
    pending_id: str = ""
    notice: str = ""  # e.g. the 80% quota warning

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "report": self.report, "request_id": self.request_id,
                "pending_id": self.pending_id, "notice": self.notice}


def request_key(request: str) -> str:
    """Normalised request text used to find a verified report for a repeat question."""
    return " ".join(re.sub(r"[^\w\s]", " ", request.lower()).split())


class Assistant:
    """One assistant per client: it holds that client's own database only."""

    def __init__(self, layer: SemanticLayer, queries: QueryService, planner: Planner, store: AppStore,
                 reporting_db: Path, freshness_hours: dict[str, float] | None = None,
                 today: date | None = None, client_id: str = "demo-client",
                 confirm_new_requests: bool = False, defer_summary: bool = False,
                 monthly_token_quota: int = 0, quota_warning_ratio: float = 0.8):
        self.layer = layer
        self.queries = queries
        self.planner = planner
        self.store = store
        self.reporting_db = reporting_db
        self.freshness_hours = freshness_hours or {"inventory": 1.0, "finance": 24.0}
        self._today = today
        self.client_id = client_id
        self.confirm_new_requests = confirm_new_requests
        self.defer_summary = defer_summary
        self.monthly_token_quota = monthly_token_quota
        self.quota_warning_ratio = quota_warning_ratio

    @property
    def today(self) -> date:
        return self._today or date.today()

    # Prompt context

    def _context(self, user: User) -> str:
        label = "T-SQL" if self.queries.dialect == "tsql" else "SQLite"
        return self.layer.describe_for(user.role, label) + f"\n\nThe user: {user.name}. {user.describe_scope()}."

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

    # Token quota (proposal: Token Metering)

    def quota_status(self, client_id: str, estimate: int = 0) -> dict:
        used = int(self.store.monthly_usage(client_id)["billable_tokens"])
        limit = self.monthly_token_quota
        status = {"limit": limit, "used": used, "blocked": False, "warning": False}
        if limit > 0:
            status["blocked"] = used >= limit
            status["warning"] = used + estimate >= limit * self.quota_warning_ratio
        return status

    # Main entry points

    def handle(self, user: User, session_id: str, request: str) -> Reply:
        """A new request from the chat panel."""
        ctx = _Context(self, user, session_id, request, uuid.uuid4().hex)
        if user.client_id != self.client_id:
            # Never expected: the portal routes each user to their own client's assistant.
            return ctx.finish("decline", "rejected", "Sorry, I can't run that report.", detail="client mismatch")
        context = self._context(user)
        try:
            quota = self.quota_status(user.client_id, count_tokens(context + request))
            if quota["blocked"]:
                return ctx.finish("blocked", "blocked", "Your organisation has used its AI reporting allowance for "
                                  "this month. Ask your administrator to upgrade the package.")
            if quota["warning"] and quota["limit"]:
                ctx.notice = (f"Your organisation has used {quota['used'] / quota['limit']:.0%} of this month's "
                              "AI reporting allowance.")

            verified = self._find_verified(user, request)
            if verified:
                plan = Plan.model_validate(verified["plan"])
                ctx.verified = True
            else:
                plan = self.planner.plan(context, self._request_text(user, session_id, request))
            ctx.module = plan.domain
            if plan.action != "query":
                kind = "clarify" if plan.action == "clarify" else "decline"
                # Kept in the history so the user's answer to a question makes sense (FR-03).
                self.store.add_turn(session_id, user.username, request, plan.message, "")
                return ctx.finish(kind, kind, plan.message, detail=plan.message)

            if self.confirm_new_requests and not verified:
                self.queries.validate(plan.sql, user)  # never ask to confirm a query that would be refused
                pending_id = self.store.save_pending(request_id=ctx.request_id, client_id=user.client_id,
                                                     username=user.username, session_id=session_id,
                                                     request=request, plan=plan.model_dump())
                ctx.pending_id = pending_id
                return ctx.finish("confirm", "awaiting_confirmation", self.restate(plan), detail=plan.sql)

            return self._execute(ctx, plan, context)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a clear reply and a log row
            return ctx.fail(exc)

    def confirm(self, user: User, pending_id: str) -> Reply:
        """The user confirmed a restated request: run the plan they saw."""
        pending = self.store.take_pending(pending_id, user.client_id, user.username)
        if pending is None:
            return Reply("error", "That request was already run or has expired. Please ask again.")
        ctx = _Context(self, user, pending["session_id"], pending["request"], pending["request_id"])
        plan = Plan.model_validate(pending["plan"])
        ctx.module = plan.domain
        try:
            if self.quota_status(user.client_id)["blocked"]:
                return ctx.finish("blocked", "blocked", "Your organisation has used its AI reporting allowance for "
                                  "this month. Ask your administrator to upgrade the package.")
            return self._execute(ctx, plan, self._context(user))
        except Exception as exc:  # noqa: BLE001
            return ctx.fail(exc)

    @staticmethod
    def restate(plan: Plan) -> str:
        """Confirm before running: what the assistant understood, in plain words."""
        parts = [f"Here is what I understood: {plan.title}."]
        if plan.filters_summary:
            parts.append(f"Filters: {plan.filters_summary}.")
        if plan.date_start or plan.date_end:
            parts.append(f"Dates: {plan.date_start or 'start'} to {plan.date_end or 'today'}.")
        parts.append("Shall I run it? If not, tell me what to change.")
        return " ".join(parts)

    def _execute(self, ctx: "_Context", plan: Plan, context: str) -> Reply:
        user = ctx.user
        try:
            result = self.queries.run(plan.sql, user)
        except QueryFailed as first_error:
            # One self-correction attempt with the database error fed back.
            plan = self.planner.plan(context, self._request_text(user, ctx.session_id, ctx.request, str(first_error)))
            ctx.verified = False
            if plan.action != "query":
                kind = "clarify" if plan.action == "clarify" else "decline"
                return ctx.finish(kind, plan.action, plan.message, detail=plan.message)
            result = self.queries.run(plan.sql, user)

        report = self._build_report(user, ctx.request, plan, result, ctx)
        self.store.save_report(report)
        self.store.add_turn(ctx.session_id, user.username, ctx.request, plan.title, result.sql)
        ctx.report_id = report["id"]
        return ctx.finish("report", "success", report=report, sql=result.sql, row_count=len(result.frame),
                          audit_outcome="report")

    # Two-step results: table and chart first, summary afterwards

    def summarize(self, report: dict) -> str:
        if not report.get("summary_pending"):
            return report.get("narrative") or ""
        if report["row_count"] == 0:
            narrative = "The query returned no rows for these filters."
        else:
            try:
                narrative = self.planner.narrate(self._summary_payload(report))
            except Exception:  # the report is still useful without the summary
                narrative = ""
        self.store.log_usage(request_id=report.get("request_id") or uuid.uuid4().hex, client_id=self.client_id,
                             user_id=report["username"], module=report.get("domain", ""), status="success",
                             usage=take_usage(self.planner))
        report["narrative"] = narrative
        report["summary_pending"] = False
        self.store.update_report(report)
        return narrative

    @staticmethod
    def _summary_payload(report: dict) -> dict:
        """The model sees totals and a few sample rows, never the full result."""
        rows = [dict(zip(report["columns"], row)) for row in report["rows"][:SUMMARY_SAMPLE_ROWS]]
        return {
            "request": report["request"],
            "title": report["title"],
            "filters": report["source"]["filters"],
            "statistics": report["statistics"],
            "first_rows": rows,
            "row_count": report["row_count"],
            "provisional": report["provisional"],
            "truncated": report["truncated"],
        }

    # Verified report library

    def _find_verified(self, user: User, request: str) -> dict | None:
        verified = self.store.find_verified(user.client_id, request_key(request))
        if not verified:
            return None
        module = verified["module"]
        needed = {"inventory", "finance"} if module == "mixed" else {module}
        if not needed <= set(user.role.domains):
            return None  # the planner will decline it in the normal way
        return verified

    def verify(self, report: dict, verified_by: str) -> str:
        """Save a report checked by our team; repeat questions reuse its query."""
        plan = report.get("plan")
        if not plan:
            raise ValueError("This report cannot be verified (no plan stored).")
        return self.store.save_verified(client_id=self.client_id, request_key=request_key(report["request"]),
                                        module=plan["domain"], request=report["request"], title=report["title"],
                                        plan=plan, verified_by=verified_by)

    # Report assembly

    def _build_report(self, user: User, request: str, plan: Plan, result, ctx: "_Context") -> dict:
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

        report = {
            "id": uuid.uuid4().hex[:12],
            "request_id": ctx.request_id,
            "client_id": user.client_id,
            "username": user.username,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request": request,
            "title": plan.title,
            "narrative": None,
            "summary_pending": True,
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
            "verified": ctx.verified,  # reused from the verified report library
            "plan": plan.model_dump(),
        }
        if not self.defer_summary:
            self.summarize_inline(report)
        return report

    def summarize_inline(self, report: dict) -> None:
        """Summary in the same response (used when results are not shown in two steps)."""
        if report["row_count"] == 0:
            report["narrative"] = "The query returned no rows for these filters."
        else:
            try:
                report["narrative"] = self.planner.narrate(self._summary_payload(report))
            except Exception:
                report["narrative"] = ""
        report["summary_pending"] = False

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


class _Context:
    """One request: its id, audit fields and how it ends. Every ending writes
    one audit row and one usage row, so 100% of requests are logged."""

    def __init__(self, assistant: Assistant, user: User, session_id: str, request: str, request_id: str):
        self.assistant = assistant
        self.user = user
        self.session_id = session_id
        self.request = request
        self.request_id = request_id
        self.started = time.monotonic()
        self.module = "none"
        self.verified = False
        self.notice = ""
        self.pending_id = ""
        self.report_id = ""

    def finish(self, kind: str, status: str, message: str = "", *, report: dict | None = None, detail: str = "",
               sql: str = "", row_count: int = 0, audit_outcome: str = "") -> Reply:
        store = self.assistant.store
        store.log_usage(request_id=self.request_id, client_id=self.user.client_id, user_id=self.user.username,
                        module=self.module, status=status, usage=take_usage(self.assistant.planner))
        store.audit(username=self.user.username, role=self.user.role.name, session_id=self.session_id,
                    request=self.request, outcome=audit_outcome or status, sql=sql, row_count=row_count,
                    report_id=self.report_id, duration_ms=int((time.monotonic() - self.started) * 1000),
                    detail=detail, client_id=self.user.client_id, request_id=self.request_id)
        return Reply(kind, message, report, self.request_id, self.pending_id, self.notice)

    def fail(self, exc: Exception) -> Reply:
        if isinstance(exc, QueryRejected):
            return self.finish("decline", "blocked", f"Sorry, I can't run that report. {exc}", detail=str(exc),
                               audit_outcome="rejected")
        if isinstance(exc, QueryFailed):
            return self.finish("error", "failed", "I couldn't build a working query for that request. Try rephrasing "
                               "it, or name the period, site or entity you want.", detail=str(exc),
                               audit_outcome="error")
        if isinstance(exc, LLMRefused):
            return self.finish("decline", "declined", "Sorry, I can't help with that request.", detail=str(exc),
                               audit_outcome="refused")
        # The AI service failed (bad key, model name, network, outage).
        return self.finish("error", "failed", f"The AI service couldn't answer: {exc}",
                           detail=f"{type(exc).__name__}: {exc}", audit_outcome="error")
