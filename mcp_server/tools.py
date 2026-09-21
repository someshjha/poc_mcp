"""The 8 MCP tools. Postgres -- not this code -- enforces the access
boundary; every tool routes its query through mcp_server.db.scoped_cursor
and reports whatever Postgres decided."""
import time

import psycopg2
from mcp.server.auth.middleware.auth_context import get_access_token

from . import db
from .scope import TASK_SCOPE_ROLES, resolve_scope

# Used only to satisfy scoped_cursor/audit's SET LOCAL ROLE mechanics when a
# caller's token carries no recognized task-scope role -- all four roles
# have identical INSERT/SELECT grants on audit_log, so which one runs this
# one INSERT is immaterial. The audited row's `scope` column still records
# the honest truth (see _NO_SCOPE_MARKER below), not this fallback name.
_FALLBACK_ROLE_FOR_AUDIT = TASK_SCOPE_ROLES[0]
_NO_SCOPE_MARKER = "none"

SCHEMA = {
    "accounts": ["account_id", "owner_name", "account_type", "opened_at"],
    "positions": ["account_id", "ticker", "quantity", "avg_cost"],
    "orders": ["order_id", "account_id", "ticker", "side", "quantity", "status", "submitted_at"],
    "transactions": ["transaction_id", "account_id", "type", "amount", "occurred_at"],
    "market_data": ["ticker", "price", "change_pct", "as_of"],
    "fundamentals": ["ticker", "pe_ratio", "market_cap", "sector"],
}


def _caller():
    at = get_access_token()
    username = at.claims.get("preferred_username")
    role = resolve_scope(at.claims)
    return username, role


def _run(name: str, fn):
    """Run fn(conn, username, role) -> result, auditing allow/deny/error."""
    username, role = _caller()
    if role is None:
        detail = "token carries no recognized task-scope role"
        conn = db.get_connection()
        try:
            db.audit(
                conn,
                username,
                _FALLBACK_ROLE_FOR_AUDIT,
                "tool",
                name,
                "deny",
                detail,
                scope=_NO_SCOPE_MARKER,
            )
        finally:
            conn.close()
        return {"decision": "deny", "detail": detail}
    conn = db.get_connection()
    try:
        result = fn(conn, username, role)
        db.audit(conn, username, role, "tool", name, "allow", "ok")
        return {"decision": "allow", "result": result}
    except psycopg2.Error as exc:
        detail = str(exc).strip()
        db.audit(conn, username, role, "tool", name, "deny", detail)
        return {"decision": "deny", "detail": detail}
    except Exception as exc:
        detail = str(exc)
        db.audit(conn, username, role, "tool", name, "error", detail)
        return {"decision": "error", "detail": detail}
    finally:
        conn.close()


def list_tables() -> dict:
    return _run("list_tables", lambda conn, username, role: list(SCHEMA.keys()))


def describe_table(table: str) -> dict:
    def fn(conn, username, role):
        if table not in SCHEMA:
            raise ValueError(f"Unknown table: {table}")
        return {"table": table, "columns": SCHEMA[table]}
    return _run("describe_table", fn)


def get_market_snapshot(tickers: list[str] | None = None) -> dict:
    def fn(conn, username, role):
        with db.scoped_cursor(conn, username, role) as cur:
            if tickers:
                cur.execute("select ticker, price, change_pct, as_of from market_data where ticker = any(%s)", (tickers,))
            else:
                cur.execute("select ticker, price, change_pct, as_of from market_data")
            cols = ["ticker", "price", "change_pct", "as_of"]
            return [dict(zip(cols, [str(v) if not isinstance(v, (int, float, str, type(None))) else v for v in r])) for r in cur.fetchall()]
    return _run("get_market_snapshot", fn)


def get_fundamentals(ticker: str) -> dict:
    def fn(conn, username, role):
        with db.scoped_cursor(conn, username, role) as cur:
            cur.execute("select ticker, pe_ratio, market_cap, sector from fundamentals where ticker = %s", (ticker,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"Unknown ticker: {ticker}")
            return dict(zip(["ticker", "pe_ratio", "market_cap", "sector"], row))
    return _run("get_fundamentals", fn)


def get_portfolio_exposure(account_id: str) -> dict:
    def fn(conn, username, role):
        with db.scoped_cursor(conn, username, role) as cur:
            cur.execute("select account_id, ticker, quantity from positions where account_id = %s", (account_id,))
            return [dict(zip(["account_id", "ticker", "quantity"], r)) for r in cur.fetchall()]
    return _run("get_portfolio_exposure", fn)


def get_account_balance(account_id: str) -> dict:
    def fn(conn, username, role):
        with db.scoped_cursor(conn, username, role) as cur:
            cur.execute("select coalesce(sum(amount), 0) from transactions where account_id = %s", (account_id,))
            return {"account_id": account_id, "balance": float(cur.fetchone()[0])}
    return _run("get_account_balance", fn)


def place_order(account_id: str, ticker: str, side: str, quantity: float) -> dict:
    def fn(conn, username, role):
        if side not in ("buy", "sell"):
            raise ValueError(f'side must be "buy" or "sell", got "{side}"')
        if not (quantity and quantity > 0):
            raise ValueError("quantity must be a positive number")
        with db.scoped_cursor(conn, username, role) as cur:
            cur.execute("select 1 from market_data where ticker = %s", (ticker,))
            if cur.fetchone() is None:
                raise ValueError(f"Unknown ticker: {ticker}")
            order_id = f"ORD-{username}-{ticker}-{int(time.time())}"
            cur.execute(
                "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
                "values (%s, %s, %s, %s, %s, 'filled', now())",
                (order_id, account_id, ticker, side, quantity),
            )
            return {"order_id": order_id, "account_id": account_id, "ticker": ticker, "side": side, "quantity": quantity, "status": "filled"}
    return _run("place_order", fn)


def get_audit_log() -> dict:
    def fn(conn, username, role):
        with db.scoped_cursor(conn, username, role) as cur:
            cur.execute(
                "select id, at, scope, user_id, kind, name, decision, detail from audit_log "
                "where user_id = %s order by id",
                (username,),
            )
            cols = ["id", "at", "scope", "user_id", "kind", "name", "decision", "detail"]
            return [dict(zip(cols, [str(v) if not isinstance(v, (int, float, str, type(None))) else v for v in r])) for r in cur.fetchall()]
    return _run("get_audit_log", fn)
