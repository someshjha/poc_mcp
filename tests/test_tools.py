import unittest

from mcp_server import db


class ToolQueriesTest(unittest.TestCase):
    def test_get_market_snapshot_query_returns_all_four_tickers(self):
        conn = db.get_connection()
        try:
            with db.scoped_cursor(conn, "alice.research", "equity_research") as cur:
                cur.execute("select ticker, price, change_pct, as_of from market_data")
                rows = cur.fetchall()
            self.assertEqual(len(rows), 4)
        finally:
            conn.close()

    def test_get_portfolio_exposure_query_is_rls_filtered_for_bob(self):
        conn = db.get_connection()
        try:
            with db.scoped_cursor(conn, "bob.risk", "portfolio_risk") as cur:
                cur.execute("select account_id, ticker, quantity from positions")
                rows = cur.fetchall()
            account_ids = {r[0] for r in rows}
            self.assertEqual(len(rows), 6)
            self.assertEqual(account_ids, {"ACC-1001", "ACC-2001"})
        finally:
            conn.close()

    def test_get_account_balance_query_sums_transactions(self):
        conn = db.get_connection()
        try:
            with db.scoped_cursor(conn, "dave.support", "client_support") as cur:
                cur.execute("select coalesce(sum(amount), 0) from transactions where account_id = %s", ("ACC-1002",))
                balance = cur.fetchone()[0]
            self.assertEqual(float(balance), 12000.0)
        finally:
            conn.close()

    def test_place_order_query_succeeds_for_owned_account_and_rolls_back_cleanly(self):
        conn = db.get_connection()
        try:
            with self.assertRaises(RuntimeError):
                with db.scoped_cursor(conn, "carol.trader", "trade_execution") as cur:
                    cur.execute(
                        "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
                        "values (%s, %s, %s, %s, %s, 'filled', now())",
                        ("ORD-TEST-TOOLS-1", "ACC-1001", "AAPL", "buy", 1, ),
                    )
                    raise RuntimeError("force rollback so this test leaves no residue")
        finally:
            conn.close()

    def test_place_order_query_denied_for_unowned_account(self):
        conn = db.get_connection()
        try:
            with self.assertRaises(Exception):
                with db.scoped_cursor(conn, "carol.trader", "trade_execution") as cur:
                    cur.execute(
                        "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
                        "values (%s, %s, %s, %s, %s, 'filled', now())",
                        ("ORD-TEST-TOOLS-2", "ACC-1002", "AAPL", "buy", 1),
                    )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
