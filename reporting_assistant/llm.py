"""LLM orchestrator calls (proposal section 7).

Claude does two jobs and never calculates figures:

1. plan()    turns a plain-English request into a structured plan: clarify,
             decline, or a read-only SQL query plus how to chart it.
2. narrate() writes the summary from statistics the analytics module already
             calculated.

Each call is stateless: earlier turns of the conversation are passed as text in
the new user message, so no assistant turns are replayed.
"""

from __future__ import annotations

import json
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Plan(BaseModel):
    action: Literal["query", "clarify", "decline"]
    message: str
    title: str
    sql: str
    domain: Literal["inventory", "finance", "mixed", "none"]
    chart_type: Literal["bar", "line", "pie", "table"]
    x: str
    y: list[str]
    series: str
    measures: list[str]
    label_column: str
    period_column: str
    variance_pair: list[str]
    date_start: str
    date_end: str
    filters_summary: str


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


def _strings(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(Plan.model_fields),
    "properties": {
        "action": {"type": "string", "enum": ["query", "clarify", "decline"]},
        "message": _string("For clarify: the one question to ask. For decline: a polite reason. For query: empty."),
        "title": _string("Short report title, e.g. 'Stock value by site, 13 Oct 2026'."),
        "sql": _string("One SQLite SELECT over the listed views. Empty unless action is query."),
        "domain": {"type": "string", "enum": ["inventory", "finance", "mixed", "none"]},
        "chart_type": {"type": "string", "enum": ["bar", "line", "pie", "table"]},
        "x": _string("Result column for the x axis or pie labels."),
        "y": _strings("Result columns plotted as values."),
        "series": _string("Result column that splits the chart into series, or empty."),
        "measures": _strings("Numeric result columns to compute statistics for."),
        "label_column": _string("Column that names each row (used for min/max), or empty."),
        "period_column": _string("Sortable period column (e.g. YYYY-MM or fiscal 'FY2026-P03') for period-over-period change, or empty."),
        "variance_pair": _strings("[actual_column, comparison_column] for budget-vs-actual style variance, or []."),
        "date_start": _string("First calendar date the report covers, YYYY-MM-DD, or empty."),
        "date_end": _string("Last calendar date the report covers, YYYY-MM-DD, or empty."),
        "filters_summary": _string("Plain-English filters applied, e.g. 'Site WH-NORTH; fiscal Q1 FY2027 (Jul-Sep 2026); USD'."),
    },
}

PLANNER_INSTRUCTIONS = """You are the planner inside a company reporting assistant. Users ask for inventory and \
financial reports in plain English. You turn each request into a plan; you never calculate or invent numbers.

Decide the action:
- "query": the request is clear enough. Write ONE SQLite SELECT over the views listed below only.
- "clarify": a detail that changes the answer is missing and has no sensible default (e.g. which site, \
when the user can see several and the report only makes sense for one). Ask one short question. Do not \
ask about things the business rules give a default for.
- "decline": the request needs data the user's role cannot see, asks to change data, or is not a \
reporting question. Explain politely in one sentence.

SQL rules:
- SQLite dialect. Use only the views and columns listed. Never reference tables named src_* or anything else.
- Return tidy, chart-ready columns with clear snake_case aliases (e.g. stock_value_usd, month, site_id).
- Round money to 2 decimals. Order rows meaningfully (time ascending, otherwise largest first).
- For time series, produce a sortable period column such as strftime('%Y-%m', movement_date) AS month, \
or printf('FY%d-P%02d', fiscal_year, fiscal_period) AS fiscal_period_label.
- Keep results under 500 rows; aggregate rather than listing raw transactions unless asked.
- Follow the business rules exactly (fiscal calendar, currency, consolidation, closed periods, signs).

Chart: bar for comparisons across items, line for trends over time, pie only for parts of a whole with \
at most 6 slices, table when a chart would not help. Fill measures with the numeric columns that matter.
If the user is refining an earlier report ("now by month"), start from the previous SQL in the conversation."""


NO_CREDENTIALS = ("No Claude credentials found. Set ANTHROPIC_API_KEY, or choose another provider with "
                  "RA_PROVIDER (gemini, groq, openrouter or grok) and its key - see the README.")


class LLMRefused(Exception):
    pass


class Planner(Protocol):
    def plan(self, context: str, request: str) -> Plan: ...

    def narrate(self, payload: dict) -> str: ...


class ClaudePlanner:
    def __init__(self, model: str, plan_effort: str = "high", narrative_effort: str = "low",
                 client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.plan_effort = plan_effort
        self.narrative_effort = narrative_effort

    def _create(self, *, system: list[dict], content: str, effort: str, schema: dict | None = None):
        output_config: dict = {"effort": effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        try:
            response = self._send(system=system, content=content, output_config=output_config)
        except TypeError as exc:
            if "authentication" not in str(exc):
                raise
            raise RuntimeError(NO_CREDENTIALS) from exc
        if response.stop_reason == "refusal":
            raise LLMRefused("The language model declined this request.")
        return next((b.text for b in response.content if b.type == "text"), "")

    def _send(self, *, system: list[dict], content: str, output_config: dict):
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",  # a classifier decline is retried on Anthropic's recommended model
            output_config=output_config,
            system=system,
            messages=[{"role": "user", "content": content}],
        )

    def plan(self, context: str, request: str) -> Plan:
        # The schema and rules for a role are stable, so they are cached;
        # today's date and the request go in the user message.
        system = [
            {"type": "text", "text": PLANNER_INSTRUCTIONS},
            {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        ]
        text = self._create(system=system, content=request, effort=self.plan_effort, schema=PLAN_SCHEMA)
        return Plan.model_validate_json(text)

    def narrate(self, payload: dict) -> str:
        system = [{"type": "text", "text": NARRATIVE_INSTRUCTIONS}]
        content = json.dumps(payload, indent=1, default=str)
        return self._create(system=system, content=content, effort=self.narrative_effort).strip()


NARRATIVE_INSTRUCTIONS = """You write the summary at the top of a business report. You receive the user's \
request, the report title, the filters, pre-calculated statistics and the first rows of the result.

- Write 2 to 4 short sentences of plain prose (no headings, no bullet points, no markdown).
- Lead with the answer to the request.
- Use ONLY numbers that appear in the statistics or rows you were given, formatted readably \
(e.g. USD 1.24m, 12.5%). Never calculate, estimate or round to new figures yourself.
- If "provisional" is true, say the figures include an open period and may change.
- If "truncated" is true, say the result was cut to the row limit."""
