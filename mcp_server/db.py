"""Postgres access scoped by task role + user identity, mirroring
scripts/verify_scopes.py's run_as/audit pattern so the server enforces
access the same way that script already proved the database does."""
from contextlib import contextmanager

import psycopg2

from .scope import TASK_SCOPE_ROLES

DSN = "host=localhost port=5432 dbname=financial_mcp user=app_pool password=app_pool_dev_only"


def get_connection():
    return psycopg2.connect(DSN)


@contextmanager
def scoped_cursor(conn, username: str, role: str):
    """Yield a cursor running inside a transaction scoped to SET LOCAL ROLE
    + SET LOCAL app.current_user_id, committing on success, rolling back on
    exception (re-raised to the caller)."""
    assert role in TASK_SCOPE_ROLES, f"refusing to SET ROLE to unrecognized role: {role!r}"
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute(f"set local role {role}")
            cur.execute("set local app.current_user_id = %s", (username,))
            try:
                yield cur
                cur.execute("commit")
            except Exception:
                conn.rollback()
                raise


def audit(conn, username: str, role: str, kind: str, name: str, decision: str, detail: str, scope: str | None = None) -> None:
    """Insert an audit_log row. `role` always drives `SET LOCAL ROLE` (must
    be one of TASK_SCOPE_ROLES, since that's what audit_log's grants exist
    for). `scope` is what's recorded in the audit_log.scope column; it
    defaults to `role` but callers may pass an explicit value (e.g. to
    honestly record that the caller had no real task-scope role, while
    still using a valid role for the SET LOCAL ROLE mechanics)."""
    assert role in TASK_SCOPE_ROLES, f"refusing to SET ROLE to unrecognized role: {role!r}"
    if scope is None:
        scope = role
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute(f"set local role {role}")
            cur.execute("set local app.current_user_id = %s", (username,))
            cur.execute(
                "insert into audit_log (scope, user_id, kind, name, decision, detail) "
                "values (%s, %s, %s, %s, %s, %s)",
                (scope, username, kind, name, decision, detail),
            )
            cur.execute("commit")
