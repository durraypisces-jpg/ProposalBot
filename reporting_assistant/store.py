"""Application database: audit log (NFR "Auditability"), generated reports,
saved favourites (FR-12), conversation history for follow-ups (FR-07), and
from the v5 proposal: the token usage log, requests waiting for the user's
confirmation, the verified report library, flagged results and captured
documents."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, username TEXT, role TEXT, session_id TEXT,
    request TEXT, outcome TEXT, sql TEXT, row_count INTEGER, report_id TEXT, duration_ms INTEGER, detail TEXT,
    client_id TEXT, request_id TEXT);
CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, at TEXT, username TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS saved_reports (
    id TEXT PRIMARY KEY, at TEXT, username TEXT, name TEXT, request TEXT);
CREATE TABLE IF NOT EXISTS conversation (
    session_id TEXT, at TEXT, username TEXT, request TEXT, title TEXT, sql TEXT);

-- Token metering: one row per request. Every model call in the request (plan,
-- retry, summary) is added to the same row. reasoning_tokens is part of
-- output_tokens. Only successful requests are billable.
CREATE TABLE IF NOT EXISTS usage_log (
    request_id TEXT PRIMARY KEY, client_id TEXT, user_id TEXT, module TEXT, model TEXT,
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0, status TEXT, billable INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS usage_by_client ON usage_log (client_id, created_at);

-- Confirm before running: a plan waits here until the user confirms it.
CREATE TABLE IF NOT EXISTS pending_plans (
    id TEXT PRIMARY KEY, at TEXT, request_id TEXT, client_id TEXT, username TEXT, session_id TEXT,
    request TEXT, plan TEXT, used INTEGER DEFAULT 0);

-- Verified report library: queries checked by our team, reused for repeat questions.
CREATE TABLE IF NOT EXISTS verified_reports (
    id TEXT PRIMARY KEY, at TEXT, client_id TEXT, request_key TEXT, module TEXT, request TEXT,
    title TEXT, plan TEXT, verified_by TEXT, active INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS verified_by_key ON verified_reports (client_id, request_key);

-- Results users flagged as wrong; they go to the dev team and into the benchmark.
CREATE TABLE IF NOT EXISTS flags (
    id TEXT PRIMARY KEY, at TEXT, client_id TEXT, username TEXT, report_id TEXT, request TEXT,
    sql TEXT, reason TEXT, status TEXT DEFAULT 'open');

-- Document capture audit trail: original file, extracted values, check results,
-- draft created in the approval workflow, and who reviewed it.
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, at TEXT, client_id TEXT, uploaded_by TEXT, doc_type TEXT, filename TEXT,
    file_path TEXT, status TEXT, read_method TEXT, extracted TEXT, issues TEXT, draft_id TEXT,
    reviewed_by TEXT, reviewed_at TEXT, error TEXT, request_id TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AppStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._con.executescript(SCHEMA)
            # Databases created before client_id/request_id were added.
            existing = {r[1] for r in self._con.execute("PRAGMA table_info(audit_log)")}
            for column in ("client_id", "request_id"):
                if column not in existing:
                    self._con.execute(f"ALTER TABLE audit_log ADD COLUMN {column} TEXT")
            self._con.commit()

    def _write(self, sql: str, params: tuple) -> None:
        with self._lock:
            self._con.execute(sql, params)
            self._con.commit()

    def _read(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._con.execute(sql, params).fetchall()]

    # Audit log
    def audit(self, *, username: str, role: str, session_id: str, request: str, outcome: str,
              sql: str = "", row_count: int = 0, report_id: str = "", duration_ms: int = 0, detail: str = "",
              client_id: str = "", request_id: str = "") -> None:
        self._write(
            "INSERT INTO audit_log (at, username, role, session_id, request, outcome, sql, row_count, report_id,"
            " duration_ms, detail, client_id, request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), username, role, session_id, request, outcome, sql, row_count, report_id, duration_ms, detail,
             client_id, request_id),
        )

    def audit_entries(self, limit: int = 200, client_id: str | None = None) -> list[dict]:
        if client_id:
            return self._read("SELECT * FROM audit_log WHERE client_id = ? ORDER BY id DESC LIMIT ?", (client_id, limit))
        return self._read("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    # Reports
    def save_report(self, report: dict) -> None:
        self._write("INSERT INTO reports VALUES (?,?,?,?)",
                    (report["id"], _now(), report["username"], json.dumps(report, default=str)))

    def update_report(self, report: dict) -> None:
        self._write("UPDATE reports SET body = ? WHERE id = ?", (json.dumps(report, default=str), report["id"]))

    def get_report(self, report_id: str) -> dict | None:
        rows = self._read("SELECT body FROM reports WHERE id = ?", (report_id,))
        return json.loads(rows[0]["body"]) if rows else None

    # Favourites (FR-12)
    def save_favourite(self, username: str, name: str, request: str) -> str:
        favourite_id = uuid.uuid4().hex[:12]
        self._write("INSERT INTO saved_reports VALUES (?,?,?,?,?)", (favourite_id, _now(), username, name, request))
        return favourite_id

    def favourites(self, username: str) -> list[dict]:
        return self._read("SELECT id, name, request, at FROM saved_reports WHERE username = ? ORDER BY at DESC", (username,))

    def delete_favourite(self, username: str, favourite_id: str) -> None:
        self._write("DELETE FROM saved_reports WHERE id = ? AND username = ?", (favourite_id, username))

    # Conversation history (FR-07)
    def add_turn(self, session_id: str, username: str, request: str, title: str, sql: str) -> None:
        self._write("INSERT INTO conversation VALUES (?,?,?,?,?,?)", (session_id, _now(), username, request, title, sql))

    def history(self, session_id: str, username: str, limit: int = 5) -> list[dict]:
        rows = self._read(
            "SELECT request, title, sql FROM conversation WHERE session_id = ? AND username = ? ORDER BY rowid DESC LIMIT ?",
            (session_id, username, limit),
        )
        return list(reversed(rows))

    # Token usage (proposal: Token Metering)
    def log_usage(self, *, request_id: str, client_id: str, user_id: str, module: str, status: str,
                  usage: list | tuple = ()) -> None:
        """Create or add to the usage row for a request. Called for every request,
        including ones that used no tokens, so 100% of requests are logged."""
        tokens_in = sum(u.input_tokens for u in usage)
        tokens_out = sum(u.output_tokens for u in usage)
        reasoning = sum(u.reasoning_tokens for u in usage)
        models = ",".join(dict.fromkeys(u.model for u in usage))
        now = _now()
        with self._lock:
            row = self._con.execute("SELECT model FROM usage_log WHERE request_id = ?", (request_id,)).fetchone()
            if row is None:
                self._con.execute(
                    "INSERT INTO usage_log (request_id, client_id, user_id, module, model, input_tokens, output_tokens,"
                    " reasoning_tokens, llm_calls, status, billable, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (request_id, client_id, user_id, module, models, tokens_in, tokens_out, reasoning, len(usage),
                     status, int(status == "success"), now, now))
            else:
                merged = ",".join(dict.fromkeys([m for m in (row["model"] or "").split(",") if m] + models.split(",")))
                self._con.execute(
                    "UPDATE usage_log SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,"
                    " reasoning_tokens = reasoning_tokens + ?, llm_calls = llm_calls + ?, model = ?,"
                    " status = ?, billable = ?, updated_at = ?,"
                    " module = CASE WHEN ? IN ('', 'none') THEN module ELSE ? END WHERE request_id = ?",
                    (tokens_in, tokens_out, reasoning, len(usage), merged.strip(","), status, int(status == "success"),
                     now, module, module, request_id))
            self._con.commit()

    def usage_row(self, request_id: str) -> dict | None:
        rows = self._read("SELECT * FROM usage_log WHERE request_id = ?", (request_id,))
        return rows[0] if rows else None

    def usage_entries(self, client_id: str | None = None, limit: int = 200) -> list[dict]:
        if client_id:
            return self._read("SELECT * FROM usage_log WHERE client_id = ? ORDER BY created_at DESC LIMIT ?",
                              (client_id, limit))
        return self._read("SELECT * FROM usage_log ORDER BY created_at DESC LIMIT ?", (limit,))

    def monthly_usage(self, client_id: str, month: str | None = None) -> dict:
        """Billable and total tokens for a client in a month (YYYY-MM, default this month)."""
        month = month or _now()[:7]
        row = self._read(
            "SELECT COUNT(*) AS requests, COALESCE(SUM(billable), 0) AS billable_requests,"
            " COALESCE(SUM(CASE WHEN billable = 1 THEN input_tokens + output_tokens END), 0) AS billable_tokens,"
            " COALESCE(SUM(input_tokens + output_tokens), 0) AS total_tokens,"
            " COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens"
            " FROM usage_log WHERE client_id = ? AND substr(created_at, 1, 7) = ?", (client_id, month))[0]
        return {"month": month, **row}

    # Confirm before running
    def save_pending(self, *, request_id: str, client_id: str, username: str, session_id: str,
                     request: str, plan: dict) -> str:
        pending_id = uuid.uuid4().hex[:12]
        self._write("INSERT INTO pending_plans (id, at, request_id, client_id, username, session_id, request, plan)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (pending_id, _now(), request_id, client_id, username, session_id, request, json.dumps(plan)))
        return pending_id

    def take_pending(self, pending_id: str, client_id: str, username: str) -> dict | None:
        """Return a pending plan once; only to the user who asked for it."""
        with self._lock:
            row = self._con.execute(
                "SELECT * FROM pending_plans WHERE id = ? AND client_id = ? AND username = ? AND used = 0",
                (pending_id, client_id, username)).fetchone()
            if row is None:
                return None
            self._con.execute("UPDATE pending_plans SET used = 1 WHERE id = ?", (pending_id,))
            self._con.commit()
        item = dict(row)
        item["plan"] = json.loads(item["plan"])
        return item

    # Verified report library
    def save_verified(self, *, client_id: str, request_key: str, module: str, request: str, title: str,
                      plan: dict, verified_by: str) -> str:
        verified_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._con.execute("UPDATE verified_reports SET active = 0 WHERE client_id = ? AND request_key = ?",
                              (client_id, request_key))
            self._con.execute(
                "INSERT INTO verified_reports (id, at, client_id, request_key, module, request, title, plan, verified_by)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (verified_id, _now(), client_id, request_key, module, request, title, json.dumps(plan), verified_by))
            self._con.commit()
        return verified_id

    def find_verified(self, client_id: str, request_key: str) -> dict | None:
        rows = self._read("SELECT * FROM verified_reports WHERE client_id = ? AND request_key = ? AND active = 1"
                          " ORDER BY at DESC LIMIT 1", (client_id, request_key))
        if not rows:
            return None
        rows[0]["plan"] = json.loads(rows[0]["plan"])
        return rows[0]

    def verified_reports(self, client_id: str) -> list[dict]:
        return self._read("SELECT id, at, module, request, title, verified_by FROM verified_reports"
                          " WHERE client_id = ? AND active = 1 ORDER BY at DESC", (client_id,))

    def retire_verified(self, client_id: str, verified_id: str) -> None:
        self._write("UPDATE verified_reports SET active = 0 WHERE id = ? AND client_id = ?", (verified_id, client_id))

    # Flagged results
    def add_flag(self, *, client_id: str, username: str, report_id: str, request: str, sql: str, reason: str) -> str:
        flag_id = uuid.uuid4().hex[:12]
        self._write("INSERT INTO flags (id, at, client_id, username, report_id, request, sql, reason)"
                    " VALUES (?,?,?,?,?,?,?,?)", (flag_id, _now(), client_id, username, report_id, request, sql, reason))
        return flag_id

    def flags(self, client_id: str | None = None, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM flags WHERE 1 = 1", []
        if client_id:
            sql += " AND client_id = ?"
            params.append(client_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        return self._read(sql + " ORDER BY at DESC", tuple(params))

    def set_flag_status(self, flag_id: str, status: str) -> None:
        self._write("UPDATE flags SET status = ? WHERE id = ?", (status, flag_id))

    # Captured documents
    def add_document(self, *, client_id: str, uploaded_by: str, doc_type: str, filename: str, file_path: str) -> str:
        document_id = uuid.uuid4().hex[:12]
        self._write("INSERT INTO documents (id, at, client_id, uploaded_by, doc_type, filename, file_path, status)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (document_id, _now(), client_id, uploaded_by, doc_type, filename, file_path, "queued"))
        return document_id

    def update_document(self, document_id: str, **fields) -> None:
        for key in ("extracted", "issues"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key], default=str)
        columns = ", ".join(f"{key} = ?" for key in fields)
        self._write(f"UPDATE documents SET {columns} WHERE id = ?", (*fields.values(), document_id))

    def get_document(self, document_id: str, client_id: str | None = None) -> dict | None:
        sql, params = "SELECT * FROM documents WHERE id = ?", [document_id]
        if client_id:
            sql += " AND client_id = ?"
            params.append(client_id)
        rows = self._read(sql, tuple(params))
        return _decode_document(rows[0]) if rows else None

    def documents(self, client_id: str, username: str | None = None, limit: int = 100) -> list[dict]:
        sql, params = "SELECT * FROM documents WHERE client_id = ?", [client_id]
        if username:
            sql += " AND uploaded_by = ?"
            params.append(username)
        return [_decode_document(r) for r in self._read(sql + " ORDER BY at DESC LIMIT ?", (*params, limit))]


def _decode_document(row: dict) -> dict:
    for key in ("extracted", "issues"):
        row[key] = json.loads(row[key]) if row.get(key) else None
    return row
