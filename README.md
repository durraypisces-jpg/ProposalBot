# AI Reporting Assistant

The code for [`proposal/Proposal_-_Local_LLM_Report_Generation_System_v5.docx`](proposal/Proposal_-_Local_LLM_Report_Generation_System_v5.docx)
(Local LLM-Based Dynamic Report Generation System). Users ask for reports in plain English and get a table
and chart first, then a short summary, exportable to PDF, Word and Excel. The same service reads purchase
orders, purchase requests and item-creation documents and creates drafts in the approval workflow.

The core rule from the proposal: **the model writes the query and the summary, but never connects to the
database and never calculates numbers.** Our own code validates every query and runs it on the client's own
database under the user's role. Every request's tokens are logged against the client's account.

It runs today against generated sample data (two clients, each with its own database: 3 warehouses, 40 items,
3 legal entities in USD, PKR and AED), so it can be shown before the client's systems are connected. In
production the model runs on our own GPU server with vLLM (`RA_PROVIDER=vllm`); the prototype can also use
Claude or a free hosted model for testing on sample data.

## Proposal to code

| Proposal section | What the code does | Where |
| --- | --- | --- |
| Model selection | vLLM provider for Arctic-Text2SQL-R1-7B and Qwen3.8-27B; optional second model for summaries (`RA_SUMMARY_MODEL`); the model's thinking is stripped from the answer | `openai_compatible.py`, `deploy/vllm/serve.sh` |
| SQL Server / T-SQL | `RA_SQL_DIALECT=tsql` switches the prompt to T-SQL (TOP, not LIMIT) and the validator to the T-SQL parser | `llm.py` (`planner_instructions`), `sql_validator.py` |
| Report Service steps 1-6 | Session -> prompt with allowed views -> model -> validate -> run under the user's role, one self-correction on error -> log tokens, SQL, time, outcome | `assistant.py` |
| Confirm before running | New requests are restated in plain words ("Stock value by site; latest snapshot...") and run only after the user confirms | `Assistant.handle` / `confirm`, `/api/chat/confirm` |
| Ask or decline instead of guessing | Planner actions `clarify` and `decline` | `llm.py`, `assistant.py` |
| Verified report library | An admin marks a checked report as verified; repeat questions reuse its query without the model | `Assistant.verify`, `/api/admin/verified` |
| Flag a wrong result | "Flag as wrong" on every report; flags list for the team; `cli flags` turns them into benchmark question stubs | `/api/reports/{id}/flag`, `cli.py` |
| Table and chart first, summary after | The report returns without the summary; the portal fetches it separately | `/api/reports/{id}/summary`, `app.js` |
| Summary sees only totals or a few rows | The summary call gets the statistics and at most 10 sample rows | `Assistant._summary_payload` |
| Layer 1: identity from the session | The client and role come from the login token; each client has its own assistant and database | `auth.py`, `app.py` (`assistant_for`) |
| Layer 2: database per client + Row-Level Security | Prototype: per-user filtered views. SQL Server: role, sites and entities set in `SESSION_CONTEXT` on every request and cleared afterwards (safe with connection pooling) | `query_service.py`, `sqlserver.py`, `deploy/sqlserver/rls_setup.sql` |
| Layer 3: SQL validator | Real parser (sqlglot): one SELECT only, no writes or commands anywhere, approved views only, no system tables or outside functions; row and time limits | `sql_validator.py` |
| Layer 4: least-privilege account | Read-only connection; SQL Server login with SELECT on the reporting schema only | `query_service.py`, `rls_setup.sql` |
| Token metering | One usage row per request: request_id, client_id, user_id, module, model, input/output/reasoning tokens, status. Counts come from the model server; every call in a request (plan, retry, summary) adds to the same row | `store.py` (`usage_log`), `llm.py` (`Usage`) |
| Tokenizer (estimates only) | The deployed model's own Hugging Face tokenizer when installed, for the quota check; never for billing | `tokens.py` |
| Quotas | Per client per month: warning at 80%, blocked at 100%. Failed or blocked requests are not charged | `Assistant.quota_status`, `RA_MONTHLY_TOKEN_QUOTA` |
| Document capture | Read digital PDF text or the scan image (OCR fallback); fixed template per type with structured JSON output; rule checks in code; draft in the approval workflow through the portal API, or the review screen | `documents.py`, `/api/documents` |
| Document checks | Line totals and order total to the cent, supplier and items in master data, no duplicate PO number, required fields, valid dates and currency, price-list comparison | `documents.check_document` |
| Audit log | Every request: user, question, SQL, rows, tokens, time, outcome. Every document: original file, extracted values, checks, draft, reviewer | `store.py` |
| Accuracy: how it is judged | Same rows and values as the correct answer (column and row order ignored unless the question asks for a sort); 100% per module; red-team must have zero breaches | `eval/run_eval.py`, `eval/questions.yaml`, `eval/redteam.yaml` |

## Quick start

**Easiest way to connect an AI:** run the setup wizard once. It asks which AI to use, takes your key
(hidden as you type), finds a model that is answering right now, and saves everything to `.env` (never
committed), so every new terminal picks it up:

```bash
pip install -r requirements.txt
python -m reporting_assistant.cli setup     # choose Gemini (free key), Ollama (no key) or Claude
python -m reporting_assistant.cli check     # plain-English test of the connection
```

Or set it up by hand:

```bash
pip install -r requirements.txt

# Option A: offline demo (standard reports only, no API key needed)
RA_DEMO_MODE=1 python -m uvicorn reporting_assistant.app:app --reload

# Option B: full assistant with Claude (any plain-English request)
export ANTHROPIC_API_KEY=...        # or sign in with `ant auth login`
python -m uvicorn reporting_assistant.app:app --reload

# Option C: production setup, a local model on our own GPU server (see deploy/vllm/serve.sh)
export RA_PROVIDER=vllm VLLM_BASE_URL=http://gpu-server:8001/v1
export RA_LLM_MODEL=arctic-text2sql-r1-7b          # writes the SQL
export RA_SUMMARY_MODEL=qwen3.8-27b                 # optional: writes summaries (another vLLM server/port)
python -m uvicorn reporting_assistant.app:app --reload

# Option D: full assistant with a free key, for sample data only (Google Gemini shown)
export RA_PROVIDER=gemini           # or groq, openrouter, grok
export GEMINI_API_KEY=...           # GROQ_API_KEY, OPENROUTER_API_KEY or XAI_API_KEY for the others
export RA_LLM_MODEL=...             # a model name from that provider's list
python -m uvicorn reporting_assistant.app:app --reload
```

| `RA_PROVIDER` | Key variable | Where to get a key |
| --- | --- | --- |
| `vllm` | `VLLM_API_KEY` (optional) | Our own GPU server; nothing leaves our servers |
| `gemini` | `GEMINI_API_KEY` | aistudio.google.com (free tier, no card) |
| `groq` | `GROQ_API_KEY` | console.groq.com (free tier) |
| `openrouter` | `OPENROUTER_API_KEY` | openrouter.ai (some free models) |
| `grok` | `XAI_API_KEY` | console.x.ai |
| `ollama` | none | Free model running on your own machine or codespace, see below |

If you leave `RA_LLM_MODEL` empty, the error message lists the model names your key can use.

**Completely free, no key: Ollama.** Runs an open-source model on the same machine, so nothing leaves it.
Slower on a CPU and less accurate than the hosted models, but it has no quotas or region restrictions:

```bash
curl -fsSL https://ollama.com/install.sh | sh     # install once
ollama serve > /tmp/ollama.log 2>&1 &             # start it in the background
ollama pull qwen2.5:3b                            # download a small model (about 2 GB)
export RA_PROVIDER=ollama RA_LLM_MODEL=qwen2.5:3b
python -m eval.run_eval
```

Every provider gets the same instructions, schema and guardrails. Only the model that writes the query and
the summary changes, so you can run the gate tests (`python -m eval.run_eval`) on each and compare accuracy.
Free tiers usually let the provider use what you send to improve their products. That is fine for the sample
data, but not for a client's real figures.

Open http://localhost:8000 and pick a demo user. Each user has a different role, so you can see access
control at work. For example, Amina only sees the North warehouse, Sara only sees the Pakistan entity's
finance data, and Bilal belongs to a second client (Beta Bank) with its own database. Omar and the
administrator can also capture documents (sidebar: Documents).

On first start, the app creates the sample ERP database and the reporting data store in `var/`.

Command line:

```bash
python -m reporting_assistant.cli init                       # rebuild sample data
python -m reporting_assistant.cli refresh                    # refresh the reporting store (schedule this)
python -m reporting_assistant.cli ask --user omar "Stock value by site"
python -m reporting_assistant.cli usage                      # token usage this month, per client
python -m reporting_assistant.cli flags                      # flagged results as benchmark stubs

# Document capture without the portal
python -m reporting_assistant.cli sample-document --type purchase_order --out po.pdf
python -m reporting_assistant.cli process-document --user omar --type purchase_order po.pdf
python -m reporting_assistant.cli sample-document --mistake --number PO-2 --out bad.pdf   # goes to review
```

## Security model

Isolation is enforced by our code and the database, not by trusting the model:

- **Identity from the session.** The client, user and role come from the login token. "Show me client 55's
  data" in the chat changes nothing: each client has its own assistant and database.
- **SQL validator.** Every query is parsed with sqlglot before it runs (see the table above).

- The reporting store is opened **read-only**.
- For each request the query service creates **TEMP views** that already filter rows to the user's sites or
  entities. The LLM only ever sees and queries those view names.
- A **SQLite authorizer** allows only `SELECT` and reads made through those views. Writes, `PRAGMA`,
  `ATTACH`, direct reads of base tables and multiple statements are refused. The tests in
  `tests/test_query_service.py` cover each case.
- Views outside a role's domains (for example, finance views for inventory roles) don't exist for that user.
  Consolidated views are only created for roles allowed to see group figures.

For SQL Server, `sqlserver.py` and `deploy/sqlserver/rls_setup.sql` add Row-Level Security: the user's role,
sites and entities are set in `SESSION_CONTEXT` before each query and cleared afterwards, so a reused (pooled)
connection never carries the previous user's role. Views are created `WITH SCHEMABINDING` and run as the
caller. The script ends with the checks to run in the red-team test.

## Benchmark and release gate (accuracy)

```bash
python -m eval.run_eval                      # every module + the red-team test
python -m eval.run_eval --module inventory   # one module
python -m eval.run_eval --redteam-only       # security tests only
python -m eval.run_eval --json results.json  # save every result for hand review
```

As in the proposal: the assistant answers each question in `eval/questions.yaml` as that question's user, and
the data engineer's correct SQL runs as the same user. A question is correct when both return the same rows and
values (column and row order ignored unless `ordered: true`; extra columns are listed for review). A module
passes only at **100%**, and only if every case in `eval/redteam.yaml` is refused (zero breaches). The runner
exits non-zero otherwise, so it can block a release. It also prints time and tokens per question.

Replace the sample questions with the 200 real questions from past custom-report tickets. Flagged results
(`python -m reporting_assistant.cli flags`) are added to the same file.

## Tests

```bash
pytest
```

The tests don't call a model: a fake planner returns scripted plans, so they run offline in a few seconds.
They cover the guardrails and SQL validator, row-level security, client isolation, statistics, finance rules,
the request flow (confirm, clarify, decline, SQL repair, follow-ups, provisional periods, verified reports,
two-step summaries), token metering and quotas, the SQL Server runner (with a fake connection), document
capture and its checks, the API, the exports and the benchmark runner.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `RA_DATA_DIR` | `var` | Where the ERP, reporting store and app databases live |
| `RA_PROVIDER` | `claude` | `vllm` (production), `claude`, `gemini`, `groq`, `openrouter`, `grok`, `ollama` or `demo` |
| `VLLM_BASE_URL` | `http://localhost:8001/v1` | The vLLM server |
| `RA_SUMMARY_MODEL` | same as `RA_LLM_MODEL` | Second model for summaries |
| `RA_DOCUMENT_MODEL` | same as `RA_LLM_MODEL` | Vision model for scanned documents |
| `RA_SQL_DIALECT` | `sqlite` | `tsql` for SQL Server |
| `RA_CONFIRM_NEW` | `1` | Restate new requests and wait for the user to confirm |
| `RA_DEFER_SUMMARY` | `1` | Show the table and chart first, then the summary |
| `RA_MONTHLY_TOKEN_QUOTA` | `0` (no limit) | Tokens per client per month; warn at `RA_QUOTA_WARNING` (0.8), block at 100%. A client's `monthly_token_quota` in the semantic layer overrides it |
| `RA_PORTAL_API_URL` / `RA_PORTAL_API_TOKEN` | empty | The portal's approval-workflow API for document drafts. Empty: a local stand-in |
| `RA_SQLSERVER_<CLIENT_ID>` | none | SQL Server connection string per client, e.g. `RA_SQLSERVER_DEMO_CLIENT` |
| `RA_MODEL` | `claude-opus-5-5` | Claude model for planning and narrative |
| `RA_LLM_MODEL` | none | Model name for the non-Claude providers (`RA_GROK_MODEL` also works) |
| `RA_PLAN_EFFORT` / `RA_NARRATIVE_EFFORT` | `high` / `low` | Effort for each call |
| `RA_DEMO_MODE` | off | Answer the standard reports without calling Claude |
| `RA_QUERY_TIMEOUT` | `30` | Seconds before a query is stopped |
| `RA_MAX_ROWS` | `5000` | Row limit per query |
| `RA_INVENTORY_REFRESH_HOURS` / `RA_FINANCE_REFRESH_HOURS` | `1` / `24` | Freshness targets; older data is flagged on reports |
| `RA_SECRET` | random per start | Signing key for session tokens; set it so sign-ins survive restarts |

Claude requests use `fallbacks: "default"`, so a safety-classifier decline is retried on Anthropic's
recommended fallback model instead of failing the report.

## From prototype to production

What is built and tested here, and what needs the client's systems:

1. **Model server.** Install vLLM on the GPU server and start the models with `deploy/vllm/serve.sh`. Set
   `RA_PROVIDER=vllm`. Run the benchmark on both candidate models and keep the winner.
2. **SQL Server.** Run `deploy/sqlserver/rls_setup.sql` on each client's reporting copy, adapted to its tables.
   `SqlServerQueryService` is written and unit-tested with a fake connection; connecting it to the app needs
   a real server to test against. Two other parts still read the prototype's SQLite store and need a
   SQL Server version: the "data as of" times and the open-period check (`assistant.py`).
3. **Semantic layer.** Rewrite `semantic_layer.yaml` for the real views: a shared base for the standard
   product tables plus each client's custom tables, and 20-50 example questions per module.
4. **SAP sync.** Schedule `refresh` (or the client's own extraction) into each reporting copy.
5. **Single sign-on.** Validate the portal's login token and map its claims, including `client_id`, through
   `auth.user_from_claims`.
6. **Approval workflow.** Agree the two endpoints in `PortalApprovalWorkflow` with the portal team and set
   `RA_PORTAL_API_URL`. Document capture then creates real drafts; the workflow still posts to SAP.
7. **Benchmark.** Load the 200 real questions and their checked answers, then run `eval/run_eval.py` before
   each module's release and after every model, prompt or schema change.
8. **Pricing.** The usage log records everything from the first test; set quotas once pricing is agreed.
