import unittest

from mcp_server import db


class ScopedCursorTest(unittest.TestCase):
    def test_scoped_cursor_allows_a_granted_table(self):
        conn = db.get_connection()
        try:
            with db.scoped_cursor(conn, "alice.research", "equity_research") as cur:
                cur.execute("select count(*) from market_data")
                self.assertEqual(cur.fetchone()[0], 4)
        finally:
            conn.close()

    def test_scoped_cursor_denies_an_ungranted_table(self):
        conn = db.get_connection()
        try:
            with self.assertRaises(Exception):
                with db.scoped_cursor(conn, "alice.research", "equity_research") as cur:
                    cur.execute("select count(*) from positions")
        finally:
            conn.close()

    def test_audit_writes_a_row(self):
        conn = db.get_connection()
        try:
            db.audit(conn, "alice.research", "equity_research", "tool", "unit_test_probe", "allow", "ok")
            with db.scoped_cursor(conn, "alice.research", "equity_research") as cur:
                cur.execute("select count(*) from audit_log where name = 'unit_test_probe'")
                self.assertGreaterEqual(cur.fetchone()[0], 1)
        finally:
            conn.close()

    def test_scoped_cursor_rejects_an_unrecognized_role(self):
        conn = db.get_connection()
        try:
            with self.assertRaises(AssertionError):
                with db.scoped_cursor(conn, "alice.research", "superuser"):
                    pass
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
