"""Application database: audit log (NFR "Auditability"), generated reports,
saved favourites (FR-12) and conversation history for follow-ups (FR-07)."""

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
    request TEXT, outcome TEXT, sql TEXT, row_count INTEGER, report_id TEXT, duration_ms INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, at TEXT, username TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS saved_reports (
    id TEXT PRIMARY KEY, at TEXT, username TEXT, name TEXT, request TEXT);
CREATE TABLE IF NOT EXISTS conversation (
    session_id TEXT, at TEXT, username TEXT, request TEXT, title TEXT, sql TEXT);
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

    def _write(self, sql: str, params: tuple) -> None:
        with self._lock:
            self._con.execute(sql, params)
            self._con.commit()

    def _read(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._con.execute(sql, params).fetchall()]

    # Audit log
    def audit(self, *, username: str, role: str, session_id: str, request: str, outcome: str,
              sql: str = "", row_count: int = 0, report_id: str = "", duration_ms: int = 0, detail: str = "") -> None:
        self._write(
            "INSERT INTO audit_log (at, username, role, session_id, request, outcome, sql, row_count, report_id, duration_ms, detail)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), username, role, session_id, request, outcome, sql, row_count, report_id, duration_ms, detail),
        )

    def audit_entries(self, limit: int = 200) -> list[dict]:
        return self._read("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    # Reports
    def save_report(self, report: dict) -> None:
        self._write("INSERT INTO reports VALUES (?,?,?,?)",
                    (report["id"], _now(), report["username"], json.dumps(report, default=str)))

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
