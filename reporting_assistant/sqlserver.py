"""Query service for Microsoft SQL Server (the client's database; proposal:
"Data Pipeline" and "Security, Data Isolation and Governance").

Same interface as query_service.QueryService (validate, run, dialect), so the
assistant does not change. It connects to the read-only reporting copy of the
logged-in client's own database, and only that one.

Isolation, layer by layer:
1. Identity from the session: the connection string is chosen by client_id
   from the login token, never from the chat.
2. Row-Level Security: before each query the user's role, sites and entities
   are set in SESSION_CONTEXT; the security policy in
   deploy/sqlserver/rls_setup.sql filters rows with them. They are set on
   every request and cleared afterwards, so a pooled connection never keeps
   the previous user's role.
3. The SQL validator (T-SQL dialect) runs before anything reaches the server.
4. The login has SELECT on the reporting schema only (see the setup script).

Needs `pyodbc` and the Microsoft ODBC driver on the server.
"""

from __future__ import annotations

import time
from typing import Callable

import pandas as pd

from .auth import User
from .query_service import QueryFailed, QueryRejected, QueryResult, _clean
from .semantic import SemanticLayer
from .sql_validator import SQLValidationError, validate_select

SESSION_KEYS = ("ra_user", "ra_role", "ra_sites", "ra_entities")
PERMISSION_DENIED = (229, 230, 262, 297, 300)  # SQL Server "permission denied" error numbers


def _pyodbc_connect(connection_string: str):
    import pyodbc  # optional dependency, only needed in production

    # Readonly connections also suit an Always On readable secondary (ApplicationIntent=ReadOnly).
    return pyodbc.connect(connection_string, autocommit=True, readonly=True)


class SqlServerQueryService:
    dialect = "tsql"

    def __init__(self, connection_string: str, layer: SemanticLayer, timeout_seconds: float = 30.0,
                 max_rows: int = 5000, reporting_schema: str = "rpt",
                 connect: Callable[[str], object] | None = None):
        self.connection_string = connection_string
        self.layer = layer
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.reporting_schema = reporting_schema
        self._connect = connect or _pyodbc_connect

    def validate(self, sql: str, user: User) -> str:
        text = _clean(sql)
        try:
            validate_select(text, {v.name for v in self.layer.views_for(user.role)}, "tsql", self.reporting_schema)
        except SQLValidationError as exc:
            if exc.table in self.layer.views:
                raise QueryRejected(f"Your role does not have access to {exc.table}.") from exc
            raise QueryRejected(str(exc)) from exc
        return text

    @staticmethod
    def session_values(user: User) -> dict[str, str]:
        return {"ra_user": user.username, "ra_role": user.role.name,
                "ra_sites": ",".join(user.sites), "ra_entities": ",".join(user.entities)}

    def run(self, sql: str, user: User) -> QueryResult:
        text = self.validate(sql, user)
        used = validate_select(text, {v.name for v in self.layer.views_for(user.role)}, "tsql", self.reporting_schema)
        con = self._connect(self.connection_string)
        started = time.monotonic()
        try:
            con.timeout = int(self.timeout_seconds)  # query timeout in seconds
            cursor = con.cursor()
            for key, value in self.session_values(user).items():
                cursor.execute("EXEC sys.sp_set_session_context @key = ?, @value = ?", key, value)
            try:
                cursor.execute(text)
                rows = cursor.fetchmany(self.max_rows + 1)
                columns = [d[0] for d in cursor.description or []]
            except Exception as exc:  # pyodbc.Error and friends
                raise self._translate(exc) from exc
            finally:
                for key in SESSION_KEYS:
                    cursor.execute("EXEC sys.sp_set_session_context @key = ?, @value = NULL", key)
        finally:
            con.close()
        truncated = len(rows) > self.max_rows
        frame = pd.DataFrame([tuple(r) for r in rows[: self.max_rows]], columns=columns)
        return QueryResult(sql=text, frame=frame, truncated=truncated,
                           elapsed_ms=int((time.monotonic() - started) * 1000), views_used=used)

    def _translate(self, exc: Exception) -> Exception:
        message = str(exc)
        if "HYT00" in message or "timeout" in message.lower():
            return QueryFailed(f"The query took longer than {self.timeout_seconds:.0f} seconds and was stopped.")
        if any(f"({n})" in message or f"Error {n}" in message for n in PERMISSION_DENIED) \
                or "permission was denied" in message.lower():
            return QueryRejected("Queries may only read the reporting views you have access to.")
        return QueryFailed(message)


class ClientConnections:
    """Which database each client uses: RA_SQLSERVER_<CLIENT_ID> environment
    variables hold one connection string per client (client id upper-cased,
    dashes as underscores). A client without one has no reporting database."""

    def __init__(self, environ: dict[str, str]):
        self.environ = environ

    def connection_string(self, client_id: str) -> str:
        key = "RA_SQLSERVER_" + client_id.upper().replace("-", "_")
        value = self.environ.get(key, "")
        if not value:
            raise LookupError(f"No database configured for client {client_id} (set {key}).")
        return value
