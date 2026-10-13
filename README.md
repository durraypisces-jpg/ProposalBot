# AI Reporting Assistant

A working prototype of the system described in
[`proposal/AI_Reporting_Assistant_Proposal_v1.1.pdf`](proposal/AI_Reporting_Assistant_Proposal_v1.1.pdf):
authorized users ask for inventory and financial reports in plain English and get a narrative summary,
key statistics, a chart and the data, exportable to PDF, Word and Excel.

The core design principle is the same as in the proposal: **Claude plans the query and writes the summary,
but never calculates numbers.** Every figure comes from a read-only SQL query against the client's data, and
statistics are computed in Python.

It runs today against a generated sample ERP (3 warehouses, 40 items, 3 legal entities in USD, PKR and AED),
so it can be demonstrated before the client's systems are connected.

## Quick start

```bash
pip install -r requirements.txt

# Option A: offline demo (standard reports only, no API key needed)
RA_DEMO_MODE=1 python -m uvicorn reporting_assistant.app:app --reload

# Option B: full assistant with Claude (any plain-English request)
export ANTHROPIC_API_KEY=...        # or sign in with `ant auth login`
python -m uvicorn reporting_assistant.app:app --reload

# Option C: full assistant with Grok (xAI)
export RA_PROVIDER=grok
export XAI_API_KEY=...              # from console.x.ai
export RA_GROK_MODEL=...            # a model name from the Models page of console.x.ai
python -m uvicorn reporting_assistant.app:app --reload
```

Grok and Claude get the same instructions, schema and guardrails. Only the model that writes the query and
the summary changes, so you can run the gate tests (`python -m eval.run_eval`) on each and compare accuracy.
If you use xAI's free credits, check their terms first: free-credit programmes may let the provider use your
data, which is fine for the sample data but not for a client's real figures.

Open http://localhost:8000 and pick a demo user. Each user has a different role, so you can see access
control at work. For example, Amina only sees the North warehouse, and Sara only sees the Pakistan entity's
finance data.

On first start, the app creates the sample ERP database and the reporting data store in `var/`.

Command line:

```bash
python -m reporting_assistant.cli init                       # rebuild sample data
python -m reporting_assistant.cli refresh                    # refresh the reporting store (schedule this)
python -m reporting_assistant.cli ask --user omar "Stock value by site"
```

## How it maps to the proposal

| Proposal component (section 7) | Code |
| --- | --- |
| User portal | `reporting_assistant/static/` (chat, reports, exports, saved reports) and `app.py` (API) |
| Access control | `auth.py` (roles, sites, entities) and `query_service.py` (row-level security in the database) |
| LLM orchestrator | `llm.py` (Claude), `grok.py` (Grok), `planners.py` (choice) and `assistant.py` (request flow) |
| Semantic layer | `semantic_layer.yaml` (views, columns, business rules, roles, templates) and `semantic.py` |
| Query service | `query_service.py` |
| Analytics and charts | `analytics.py` (statistics) and `exporters.py` / `app.js` (charts) |
| Report generator | `exporters.py` (PDF, Word, Excel) |
| Audit log | `store.py` (`audit_log` table; admins see it in the portal) |
| Reporting data store (new in v1.1) | `refresh.py` |
| Client data sources | `sample_data.py` (stand-in ERP until discovery) |

| Requirement | Where it is handled |
| --- | --- |
| FR-01 SSO login | Demo sign-in. `auth.user_from_claims` is the hook for Entra ID or another SSO |
| FR-02, FR-07 chat and follow-ups | `assistant.py` passes the earlier requests and their SQL to the planner |
| FR-03 clarifying questions | Planner action `clarify` |
| FR-04 to FR-06 summary, statistics, charts | `analytics.py`, `llm.py` (narrative), bar, line, pie and table charts |
| FR-08 exports | `/api/reports/{id}/export/{pdf,docx,xlsx}` |
| FR-09 role restrictions | Per-user filtered views plus the SQLite authorizer. Restricted requests are declined politely |
| FR-10 source, filters, dates, data as of | Shown on every report and in every export |
| FR-11, FR-12 templates and saved reports | `templates` in the semantic layer; favourites in `store.py` |
| FR-15 fiscal calendar | `fiscal_periods` view and business rules (fiscal year starts in July) |
| FR-16 multi-currency | `amount_usd` converted with the ERP's own average rates during refresh |
| FR-17 multi-entity | `gl_consolidated_monthly` (ERP consolidation) for group figures. Never summed across entities |
| FR-18 period status | Reports that cover an open period are marked provisional |
| FR-19 account mapping | `accounts.report_line` and sign rules in the semantic layer |
| NFR transparency | "Show query" on every report; the SQL is in every export |
| NFR source system load | 30-second time limit, row limit, and a reporting copy instead of the live ERP |
| NFR regression testing, Gates 2 and 3 | `eval/run_eval.py` with `eval/questions.yaml` |

## Security model

All of these are enforced by the database layer, not the prompt:

- The reporting store is opened **read-only**.
- For each request the query service creates **TEMP views** that already filter rows to the user's sites or
  entities. The LLM only ever sees and queries those view names.
- A **SQLite authorizer** allows only `SELECT` and reads made through those views. Writes, `PRAGMA`,
  `ATTACH`, direct reads of base tables and multiple statements are refused. The tests in
  `tests/test_query_service.py` cover each case.
- Views outside a role's domains (for example, finance views for inventory roles) don't exist for that user.
  Consolidated views are only created for roles allowed to see group figures.

## Gate tests (accuracy)

```bash
python -m eval.run_eval            # calls Claude once or twice per question
```

The runner asks each question in `eval/questions.yaml`, runs the expert's expected SQL as the same user, and
compares the numbers. It prints accuracy per gate against the targets (Gate 2: 95%, Gate 3: 98%) and exits
non-zero on a miss, so it can block a release. In discovery, replace the sample questions with 100+ questions
taken from reports the client already trusts.

## Tests

```bash
pytest
```

The tests don't call Claude: a fake planner returns scripted plans, so they run offline in a few seconds.
They cover the guardrails, row-level security, statistics, finance rules, the request flow (clarify, decline,
SQL repair, follow-ups, provisional periods), the API, the exports and the gate runner.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `RA_DATA_DIR` | `var` | Where the ERP, reporting store and app databases live |
| `RA_PROVIDER` | `claude` | `claude`, `grok` or `demo` |
| `RA_MODEL` | `claude-opus-5-5` | Claude model for planning and narrative |
| `RA_GROK_MODEL` / `XAI_API_KEY` | none | Grok model name and key, when `RA_PROVIDER=grok` |
| `RA_PLAN_EFFORT` / `RA_NARRATIVE_EFFORT` | `high` / `low` | Effort for each call |
| `RA_DEMO_MODE` | off | Answer the standard reports without calling Claude |
| `RA_QUERY_TIMEOUT` | `30` | Seconds before a query is stopped |
| `RA_MAX_ROWS` | `5000` | Row limit per query |
| `RA_INVENTORY_REFRESH_HOURS` / `RA_FINANCE_REFRESH_HOURS` | `1` / `24` | Freshness targets; older data is flagged on reports |
| `RA_SECRET` | random per start | Signing key for session tokens; set it so sign-ins survive restarts |

Claude requests use `fallbacks: "default"`, so a safety-classifier decline is retried on Anthropic's
recommended fallback model instead of failing the report.

## From prototype to production

These follow the open questions in section 12 of the proposal:

1. **Data sources.** Replace `sample_data.py` with a connector to the client's ERP or data warehouse, and
   point `refresh.py`'s transforms at it. If a warehouse already exists, it can serve as the reporting
   store directly.
2. **Single sign-on.** Validate the identity provider's token and map its claims through
   `auth.user_from_claims`.
3. **Semantic layer.** Rewrite `semantic_layer.yaml` for the real tables, fiscal calendar, chart of accounts
   and roles, and get finance to approve the mapping.
4. **Hosting.** For cloud hosting, keep Claude via the API. For on-premise hosting, swap `ClaudePlanner` for
   a self-hosted model that implements the same `Planner` interface.
5. **Scheduling.** Run `python -m reporting_assistant.cli refresh` from cron or a scheduler at the agreed
   freshness targets.
6. **Gate test set.** Build the real question set with finance and operations, then run `eval/run_eval.py`
   before each gate and after every model or schema change.
