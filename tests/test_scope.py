import unittest

from mcp_server.scope import resolve_scope


class ResolveScopeTest(unittest.TestCase):
    def test_returns_matching_task_scope_role(self):
        claims = {"realm_access": {"roles": ["offline_access", "equity_research", "uma_authorization"]}}
        self.assertEqual(resolve_scope(claims), "equity_research")

    def test_returns_none_when_no_task_scope_role_present(self):
        claims = {"realm_access": {"roles": ["offline_access", "uma_authorization"]}}
        self.assertIsNone(resolve_scope(claims))

    def test_returns_none_when_realm_access_missing(self):
        self.assertIsNone(resolve_scope({}))


if __name__ == "__main__":
    unittest.main()
