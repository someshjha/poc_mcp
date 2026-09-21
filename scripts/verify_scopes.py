"""Independent proof that Postgres itself enforces both scoping axes.

Connects as the low-privilege app_pool login role, impersonates each demo
user via SET LOCAL ROLE + SET LOCAL app.current_user_id (exactly what the
MCP server will do at runtime), and asserts every allow/deny outcome the
design specifies. Does not import or call anything from a future MCP
server -- if this script passes, the database boundary is real regardless
of what the application code does.
"""
import os
import sys

import psycopg2

DSN = os.environ.get(
    "VERIFY_DSN",
    "host=localhost port=5432 dbname=financial_mcp user=app_pool password=app_pool_dev_only",
)

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def run_as(conn, user_id, role, sql, params=None, commit_effect=True):
    """Run one statement as the given demo user/role. Returns (ok, rows_or_error).

    commit_effect=False rolls back a successful statement instead of
    committing it -- used for attempts whose side effects should not
    persist across script runs (e.g. test order inserts), while still
    reporting whether the statement itself was allowed by the database.
    """
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute(f"set local role {role}")
            cur.execute("set local app.current_user_id = %s", (user_id,))
            try:
                cur.execute(sql, params)
                rows = cur.fetchall() if cur.description else None
                if commit_effect:
                    cur.execute("commit")
                else:
                    conn.rollback()
                return True, rows
            except psycopg2.Error as exc:
                conn.rollback()
                return False, str(exc).strip()


def audit(conn, user_id, role, kind, name, decision, detail):
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute(f"set local role {role}")
            cur.execute("set local app.current_user_id = %s", (user_id,))
            cur.execute(
                "insert into audit_log (scope, user_id, kind, name, decision, detail) "
                "values (%s, %s, %s, %s, %s, %s)",
                (role, user_id, kind, name, decision, detail),
            )
            cur.execute("commit")


def attempt(conn, user_id, role, label, sql, params=None, commit_effect=True):
    ok, result = run_as(conn, user_id, role, sql, params, commit_effect=commit_effect)
    decision = "allow" if ok else "deny"
    detail = "ok" if ok else result
    audit(conn, user_id, role, "query", label, decision, detail)
    return ok, result


def main():
    conn = psycopg2.connect(DSN)

    # Capture the audit_log high-water mark BEFORE running any attempts, so
    # the end-of-run assertions can check the DELTA this run produced
    # instead of the table's total -- making the whole script safely
    # re-runnable against an already-seeded database. app_pool itself has
    # no direct grant on audit_log (only the four scope roles do, and
    # app_pool is NOINHERIT), so this read must go through one of them.
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute("set local role equity_research")
            cur.execute("select coalesce(max(id), 0) from audit_log")
            audit_start_id = cur.fetchone()[0]
            cur.execute("commit")

    # --- alice.research / equity_research ---
    ok, rows = attempt(conn, "alice.research", "equity_research", "select_market_data",
                        "select * from market_data")
    check("alice(equity_research) can select market_data", ok and len(rows) == 4)

    ok, rows = attempt(conn, "alice.research", "equity_research", "select_fundamentals",
                        "select * from fundamentals")
    check("alice(equity_research) can select fundamentals", ok and len(rows) == 4)

    ok, detail = attempt(conn, "alice.research", "equity_research", "select_positions_denied",
                          "select * from positions")
    check(
        "alice(equity_research) is denied positions (no grant)",
        not ok and "permission denied" in str(detail),
    )

    ok, detail = attempt(conn, "alice.research", "equity_research", "select_accounts_denied",
                          "select * from accounts")
    check(
        "alice(equity_research) is denied accounts (no grant)",
        not ok and "permission denied" in str(detail),
    )

    # --- bob.risk / portfolio_risk (owns ACC-1001, ACC-2001) ---
    ok, rows = attempt(conn, "bob.risk", "portfolio_risk", "select_positions_rls",
                        "select account_id from positions")
    account_ids = {r[0] for r in rows} if ok else set()
    check(
        "bob(portfolio_risk) sees only his own accounts' positions (RLS)",
        ok and len(rows) == 6 and account_ids == {"ACC-1001", "ACC-2001"},
    )

    ok, rows = attempt(conn, "bob.risk", "portfolio_risk", "select_market_data",
                        "select * from market_data")
    check("bob(portfolio_risk) can select market_data (ungated)", ok and len(rows) == 4)

    ok, detail = attempt(conn, "bob.risk", "portfolio_risk", "select_accounts_denied",
                          "select * from accounts")
    check(
        "bob(portfolio_risk) is denied accounts (no grant)",
        not ok and "permission denied" in str(detail),
    )

    ok, detail = attempt(
        conn, "bob.risk", "portfolio_risk", "insert_order_wrong_role",
        "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
        "values ('ORD-TEST-BOB', 'ACC-1001', 'AAPL', 'buy', 1, 'filled', now())",
    )
    check(
        "bob(portfolio_risk) is denied place_order for his OWN account -- wrong role, right account",
        not ok and "permission denied" in str(detail),
    )

    # --- carol.trader / trade_execution (owns ACC-1001 only) ---
    ok, rows = attempt(conn, "carol.trader", "trade_execution", "select_market_data",
                        "select * from market_data")
    check("carol(trade_execution) can select market_data", ok and len(rows) == 4)

    ok, detail = attempt(conn, "carol.trader", "trade_execution", "select_positions_wrong_role",
                          "select * from positions")
    check(
        "carol(trade_execution) is denied positions for an account SHE OWNS -- right account, wrong role",
        not ok and "permission denied" in str(detail),
    )

    # These two order-insert attempts are rolled back instead of committed
    # (commit_effect=False) so `orders` stays at its seeded 2 rows no
    # matter how many times this script runs. RLS WITH CHECK is evaluated
    # at INSERT time regardless of whether the transaction later commits
    # or rolls back, so the allow/deny outcome is unaffected.
    ok, _ = attempt(
        conn, "carol.trader", "trade_execution", "insert_order_own_account",
        "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
        "values ('ORD-TEST-CAROL-OWN', 'ACC-1001', 'AAPL', 'buy', 1, 'filled', now())",
        commit_effect=False,
    )
    check("carol(trade_execution) can place an order for ACC-1001, which she owns", ok)

    ok, detail = attempt(
        conn, "carol.trader", "trade_execution", "insert_order_not_owned",
        "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
        "values ('ORD-TEST-CAROL-OTHER', 'ACC-1002', 'AAPL', 'buy', 1, 'filled', now())",
        commit_effect=False,
    )
    check(
        "carol(trade_execution) is denied placing an order for ACC-1002, which she does NOT own (RLS)",
        not ok and "row-level security" in str(detail),
    )

    # --- dave.support / client_support (owns ACC-1002 only) ---
    ok, rows = attempt(conn, "dave.support", "client_support", "select_accounts_rls",
                        "select account_id from accounts")
    check(
        "dave(client_support) sees only ACC-1002 (RLS)",
        ok and len(rows) == 1 and rows[0][0] == "ACC-1002",
    )

    ok, rows = attempt(conn, "dave.support", "client_support", "select_transactions_rls",
                        "select transaction_id from transactions")
    check(
        "dave(client_support) sees only ACC-1002's transaction (RLS)",
        ok and len(rows) == 1 and rows[0][0] == "TXN-5003",
    )

    ok, detail = attempt(conn, "dave.support", "client_support", "select_positions_denied",
                          "select * from positions")
    check(
        "dave(client_support) is denied positions (no grant)",
        not ok and "permission denied" in str(detail),
    )

    ok, detail = attempt(conn, "dave.support", "client_support", "select_market_data_denied",
                          "select * from market_data")
    check(
        "dave(client_support) is denied market_data (no grant)",
        not ok and "permission denied" in str(detail),
    )

    # --- audit trail check, independent of the above ---
    # Assert the DELTA this run produced (rows with id > audit_start_id),
    # not the table's total, so the script is safely re-runnable without
    # wiping the database between runs. app_pool has no direct grant on
    # audit_log, so read it through one of the scope roles that does.
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute("set local role equity_research")
            cur.execute("select count(*) from audit_log where id > %s", (audit_start_id,))
            total = cur.fetchone()[0]
            cur.execute(
                "select count(*) from audit_log where id > %s and decision = 'deny'",
                (audit_start_id,),
            )
            denied = cur.fetchone()[0]
            cur.execute("commit")
    check("audit_log recorded every attempt this run (16)", total == 16)
    check("audit_log recorded the expected number of denials this run (8)", denied == 8)

    conn.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
