"""SQL validator (proposal: Security, layer 3 "SQL validator before execution").

The query is read with a real SQL parser (sqlglot), not keyword searches, so
comments, nested queries and several statements in one request are caught.

Rules:
* exactly one statement, and it must be a SELECT (UNION and WITH allowed);
* no INSERT, UPDATE, DELETE, MERGE, DROP, CREATE, ALTER, EXEC, SELECT INTO
  or any other command anywhere in the tree;
* only approved views, never schema-qualified names or system tables;
* no functions that reach outside the database (OPENROWSET and the like).

The dialect is "sqlite" for the prototype and "tsql" for SQL Server (RA_SQL_DIALECT).
Row and time limits are applied by the query runner.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError


class SQLValidationError(Exception):
    def __init__(self, message: str, table: str = ""):
        super().__init__(message)
        self.table = table


_FORBIDDEN_NODES = tuple(
    node for node in (getattr(exp, name, None) for name in (
        "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "AlterTable", "TruncateTable",
        "Command", "Into", "Pragma", "Attach", "Detach", "Use", "Grant", "Revoke", "Transaction",
        "Commit", "Rollback", "Copy", "LoadData", "Set", "Execute", "Kill", "Analyze"))
    if isinstance(node, type)
)

_FORBIDDEN_FUNCTIONS = {
    "openrowset", "opendatasource", "openquery", "openxml", "openjson_file",
    "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer",
}

_SYSTEM_PREFIXES = ("sys", "sqlite_", "information_schema", "msdb", "master", "xp_", "sp_")


def validate_select(sql: str, allowed_views: set[str], dialect: str = "sqlite",
                    reporting_schema: str = "") -> list[str]:
    """Return the views the query reads, or raise SQLValidationError.

    reporting_schema: on SQL Server the reporting views live in one schema
    (e.g. "rpt"); names qualified with that schema are accepted."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except (ParseError, TokenError) as exc:
        raise SQLValidationError(f"The query could not be read as {dialect} SQL.") from exc
    if len(statements) != 1:
        raise SQLValidationError("Only one SQL statement is allowed per query.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SQLValidationError("Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLValidationError("Only SELECT queries are allowed.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _FORBIDDEN_FUNCTIONS or name.startswith(("xp_", "sp_")):
                raise SQLValidationError(f"The function {name} is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {v.lower() for v in allowed_views}
    used: set[str] = set()
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SQLValidationError("Queries may only read the reporting views you have access to.")
        name = table.name.lower()
        schema = table.args.get("db")
        if table.args.get("catalog") or (schema and schema.name.lower() != reporting_schema.lower()):
            raise SQLValidationError("Queries may only read the reporting views you have access to.")
        if name in cte_names:
            continue
        if name.startswith(_SYSTEM_PREFIXES) or name not in allowed:
            raise SQLValidationError("Queries may only read the reporting views you have access to.", table=name)
        used.add(name)
    return sorted(used)
