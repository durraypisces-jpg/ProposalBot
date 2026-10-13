"""Query service: runs LLM-written SQL safely (proposal section 7).

Guardrails, all enforced by the database layer rather than the prompt:

* The reporting store is opened read-only.
* Every query runs against per-user TEMP views that already carry the user's
  row filters (site or entity), so a query cannot see rows outside the user's
  scope even if it tries (FR-09, NFR "Security").
* A SQLite authorizer allows only SELECT and reads made *through* those views.
  Direct table access, PRAGMA, ATTACH and writes are refused.
* One statement per request, a time limit and a row limit (NFR "Source system
  load").
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .auth import User
from .semantic import SemanticLayer

_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_LEADING_COMMENTS = re.compile(r"^\s*(--[^\n]*\n|/\*.*?\*/)\s*", re.S)


class QueryRejected(Exception):
    """The query breaks a guardrail or asks for data the user may not see."""


class QueryFailed(Exception):
    """The query is allowed but did not run (SQL error or time limit)."""


@dataclass
class QueryResult:
    sql: str
    frame: pd.DataFrame
    truncated: bool
    elapsed_ms: int
    views_used: list[str] = field(default_factory=list)


def _clean(sql: str) -> str:
    text = sql.strip()
    while True:
        stripped = _LEADING_COMMENTS.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    text = text.rstrip().rstrip(";").rstrip()
    if not re.match(r"(?is)^(select|with)\b", text):
        raise QueryRejected("Only SELECT queries are allowed.")
    return text


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class QueryService:
    def __init__(self, reporting_db: Path, layer: SemanticLayer, timeout_seconds: float = 30.0, max_rows: int = 5000):
        self.reporting_db = reporting_db
        self.layer = layer
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows

    def _connect(self, user: User) -> tuple[sqlite3.Connection, set[str], set[str], set[str]]:
        con = sqlite3.connect(f"file:{self.reporting_db}?mode=ro", uri=True, check_same_thread=False)
        base_tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
        base_tables |= {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_sequence"}
        visible = self.layer.views_for(user.role)
        names: set[str] = set()
        unfiltered_sources: set[str] = set()
        for view in visible:
            where = ""
            scope = user.role.row_filters.get(view.row_filter) if view.row_filter else None
            if scope is not None:
                values = user.scope_values(scope)
                where = f" WHERE {view.row_filter} IN ({', '.join(_literal(v) for v in values)})" if values else " WHERE 0"
            else:
                unfiltered_sources.add(view.source)
            con.execute(f"CREATE TEMP VIEW {view.name} AS SELECT * FROM main.{view.source}{where}")
            names.add(view.name)

        used: set[str] = set()

        def authorizer(action, arg1, arg2, db_name, source):
            if action in _ALLOWED_ACTIONS:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                # Columns of our temp views, or base-table reads made by a view.
                if db_name == "temp" and arg1 in names:
                    used.add(arg1)
                    return sqlite3.SQLITE_OK
                if source in names:
                    return sqlite3.SQLITE_OK
                if db_name is None and arg1 not in base_tables:
                    return sqlite3.SQLITE_OK  # columns of a CTE, which belong to no table
                if arg2 == "" and arg1 in unfiltered_sources:
                    # A row count with no column values (e.g. COUNT(*) over a
                    # flattened view); only for tables the user sees in full.
                    return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        con.set_authorizer(authorizer)
        return con, names, used, base_tables

    def run(self, sql: str, user: User) -> QueryResult:
        text = _clean(sql)
        con, visible, used, base_tables = self._connect(user)
        named = {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)}
        if named & {t.lower() for t in base_tables}:
            con.close()
            raise QueryRejected("Queries may only read the reporting views you have access to.")
        deadline = time.monotonic() + self.timeout_seconds
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        started = time.monotonic()
        try:
            cursor = con.execute(text)
            rows = cursor.fetchmany(self.max_rows + 1)
            columns = [d[0] for d in cursor.description or []]
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "interrupted" in message:
                raise QueryFailed(f"The query took longer than {self.timeout_seconds:.0f} seconds and was stopped.") from exc
            missing = re.search(r"no such table: (\w+)", message)
            if missing and missing.group(1) in self.layer.views and missing.group(1) not in visible:
                raise QueryRejected(f"Your role does not have access to {missing.group(1)}.") from exc
            raise QueryFailed(message) from exc
        except sqlite3.ProgrammingError as exc:
            raise QueryRejected("Only one SQL statement is allowed per query.") from exc
        except sqlite3.DatabaseError as exc:
            if "prohibited" in str(exc) or "not authorized" in str(exc):
                raise QueryRejected("Queries may only read the reporting views you have access to.") from exc
            raise QueryFailed(str(exc)) from exc
        finally:
            con.close()

        truncated = len(rows) > self.max_rows
        frame = pd.DataFrame(rows[: self.max_rows], columns=columns)
        return QueryResult(
            sql=text,
            frame=frame,
            truncated=truncated,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            views_used=sorted(used),
        )
