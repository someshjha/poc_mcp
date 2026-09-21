# Phase 2 — MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real MCP server, reachable over Streamable HTTP, that authenticates callers via real Keycloak JWTs and executes the 8-tool catalogue against Phase 1's Postgres schema using the exact `SET LOCAL ROLE` + `SET LOCAL app.current_user_id` pattern `scripts/verify_scopes.py` already proved — so Postgres, not this server's own code, is still what enforces the boundary.

**Architecture:** Keycloak joins `docker-compose.yml` as a new service (realm imported from a committed JSON at container start). A new `mcp_server/` Python package wraps the official `mcp` SDK's `MCPServer` (Streamable HTTP transport) with a custom `TokenVerifier` that validates Bearer tokens against Keycloak's JWKS; each tool handler resolves the caller's identity/role from the verified token's claims and runs its query through a small `db.py` helper that mirrors `verify_scopes.py`'s proven transaction-scoping pattern. `scripts/verify_mcp_server.py` re-proves the same boundary through a real MCP client, one layer above where Phase 1 proved it.

**Tech Stack:** Python, `mcp` SDK v2.x (`MCPServer`, not the deprecated `FastMCP`), `uvicorn`, `python-jose[cryptography]`, `httpx`, `psycopg2` (already used in Phase 1), Keycloak 26.0.

## Global Constraints

- The `mcp` package installs at v2.x; the server API is `mcp.server.mcpserver.MCPServer`, NOT `mcp.server.fastmcp.FastMCP` (renamed and restructured in v2 — using the old import path fails immediately with `ModuleNotFoundError`).
- `docker-compose.yml` must use standard port publishing (`-p HOST:CONTAINER` / the `ports:` key) — `--network host` does not work reliably on this host's Docker setup (confirmed: a container is unreachable on its mapped port under `--network host`, works immediately under normal port publishing).
- Every Keycloak user in `keycloak/realm-export.json` MUST include `email`, `firstName`, `lastName`, and `requiredActions: []` — omitting any of these causes password-grant login to fail with the generic, non-actionable error `"Account is not fully set up"` (Keycloak's declarative user profile, default-enabled since v21+). Confirmed directly: a user missing these fields cannot log in; the identical user with them present logs in immediately.
- Demo users and roles are fixed by Phase 1 and must match exactly: `alice.research`→`equity_research`, `bob.risk`→`portfolio_risk`, `carol.trader`→`trade_execution`, `dave.support`→`client_support`.
- A tool handler must never let a raw exception propagate out of the tool function to signal a denial — confirmed directly: the SDK swallows a raised exception's message and returns only the generic string `"Error executing tool <name>"` to the client, destroying the actual Postgres error text. Every tool must catch its own exceptions and return a structured `{"decision": "allow"|"deny"|"error", ...}` dict instead (`psycopg2.Error` → `"deny"`; any other exception, e.g. bad input → `"error"`), mirroring the three-way decision already used by the browser mock (`financial-mcp/mock-mcp-server.js` in the `someshjha.github.io` repo) and matching this repo's Postgres role/RLS semantics.
- A tool's return value (a Python `dict`) is serialized by the SDK to pretty-printed JSON text in `CallToolResult.content[0].text` — confirmed directly. Clients must `json.loads()` that text, not substring-match it.
- The MCP client library's Streamable HTTP context manager yields a **2-tuple** `(read, write)`, not 3 values — confirmed directly; unpacking 3 raises `ValueError: not enough values to unpack`.
- `CallToolResult`'s error flag is the snake_case attribute `is_error`, not `isError` (a pydantic model attribute-access error if you guess wrong) — confirmed directly.
- This phase does not containerize the MCP server itself and does not touch Kubernetes/Argo CD — those are Phases 4-5. The server runs as a plain `python3`/`uvicorn` host process for this phase.
- Postgres connection details (`app_pool` login role, `app_pool_dev_only` password, `financial_mcp` database) are exactly as Phase 1 left them — no schema/role/RLS changes in this phase.

---

### Task 1: Keycloak service + realm

**Files:**
- Create: `keycloak/realm-export.json`
- Modify: `docker-compose.yml` (add `keycloak` service)

**Interfaces:**
- Produces: a running Keycloak reachable at `http://localhost:8080`, realm `financial-mcp`, client `mcp-server` (public, direct-access-grants enabled), 4 realm roles, 4 demo users each able to obtain a password-grant access token carrying their `sub`, `preferred_username`, and `realm_access.roles` claims. Task 3's `KeycloakTokenVerifier` and Task 6's verification script both depend on this realm existing exactly as specified.

- [ ] **Step 1: Write the realm export**

Create `keycloak/realm-export.json`:

```json
{
  "realm": "financial-mcp",
  "enabled": true,
  "roles": {
    "realm": [
      {"name": "equity_research"},
      {"name": "portfolio_risk"},
      {"name": "trade_execution"},
      {"name": "client_support"}
    ]
  },
  "clients": [
    {
      "clientId": "mcp-server",
      "publicClient": true,
      "directAccessGrantsEnabled": true,
      "enabled": true
    }
  ],
  "users": [
    {
      "username": "alice.research",
      "enabled": true,
      "emailVerified": true,
      "email": "alice.research@example.com",
      "firstName": "Alice",
      "lastName": "Research",
      "requiredActions": [],
      "credentials": [{"type": "password", "value": "alice_dev_only", "temporary": false}],
      "realmRoles": ["equity_research"]
    },
    {
      "username": "bob.risk",
      "enabled": true,
      "emailVerified": true,
      "email": "bob.risk@example.com",
      "firstName": "Bob",
      "lastName": "Risk",
      "requiredActions": [],
      "credentials": [{"type": "password", "value": "bob_dev_only", "temporary": false}],
      "realmRoles": ["portfolio_risk"]
    },
    {
      "username": "carol.trader",
      "enabled": true,
      "emailVerified": true,
      "email": "carol.trader@example.com",
      "firstName": "Carol",
      "lastName": "Trader",
      "requiredActions": [],
      "credentials": [{"type": "password", "value": "carol_dev_only", "temporary": false}],
      "realmRoles": ["trade_execution"]
    },
    {
      "username": "dave.support",
      "enabled": true,
      "emailVerified": true,
      "email": "dave.support@example.com",
      "firstName": "Dave",
      "lastName": "Support",
      "requiredActions": [],
      "credentials": [{"type": "password", "value": "dave_dev_only", "temporary": false}],
      "realmRoles": ["client_support"]
    }
  ]
}
```

- [ ] **Step 2: Add the Keycloak service to docker-compose.yml**

Add this service to the `services:` block in `docker-compose.yml` (alongside the existing `postgres` and `liquibase` services — do not modify those):

```yaml
  keycloak:
    image: quay.io/keycloak/keycloak:26.0
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin_dev_only
    command: ["start-dev", "--import-realm"]
    ports:
      - "8080:8080"
    volumes:
      - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm.json:ro
```

No Docker-level healthcheck is configured for this service (Keycloak's official image does not reliably include `curl`/`wget` for one) — verification below polls the HTTP endpoint from the host instead, and nothing else in this compose file needs to `depends_on` it.

- [ ] **Step 3: Bring it up and verify each demo user can log in with the right role**

Run:
```bash
docker compose up -d keycloak
```

Poll until ready (typically ~10s):
```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/realms/financial-mcp)" = "200" ]; do sleep 1; done
echo ready
```

For each user, request a token and confirm the role claim, e.g. for `alice.research`:
```bash
curl -s -X POST http://localhost:8080/realms/financial-mcp/protocol/openid-connect/token \
  -d "grant_type=password" -d "client_id=mcp-server" -d "username=alice.research" -d "password=alice_dev_only" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if 'access_token' in d else d)"
```
Expected: `OK`. Repeat for `bob.risk`/`bob_dev_only`, `carol.trader`/`carol_dev_only`, `dave.support`/`dave_dev_only` — all four must print `OK`. If any prints an error dict instead, the realm JSON is wrong (most likely a missing required profile field per the Global Constraints note) — do not proceed until all four succeed.

- [ ] **Step 4: Commit**

```bash
git add keycloak/realm-export.json docker-compose.yml
git commit -m "$(cat <<'EOF'
Add Keycloak service and realm for Phase 2 auth

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `mcp_server` package core — `scope.py` and `db.py`

**Files:**
- Create: `mcp_server/__init__.py` (empty)
- Create: `mcp_server/scope.py`
- Create: `mcp_server/db.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_scope.py`
- Create: `tests/test_db.py`
- Modify: `scripts/requirements.txt` (add new dependencies)

**Interfaces:**
- Produces: `mcp_server.scope.TASK_SCOPE_ROLES` (tuple of the 4 role name strings), `mcp_server.scope.resolve_scope(claims: dict) -> str | None`; `mcp_server.db.get_connection() -> psycopg2.connection`, `mcp_server.db.scoped_cursor(conn, username: str, role: str)` (context manager yielding a cursor), `mcp_server.db.audit(conn, username: str, role: str, kind: str, name: str, decision: str, detail: str) -> None`. Tasks 3-4 import from both modules using exactly these names.

- [ ] **Step 1: Add the new Python dependencies**

Append to `scripts/requirements.txt` (keep the existing `psycopg2-binary>=2.9.10` line):

```
mcp[cli]>=2.2.0,<3
uvicorn>=0.30
python-jose[cryptography]>=3.3.0
httpx>=0.27
```

- [ ] **Step 2: Write the failing tests**

Create `tests/__init__.py` (empty file).

Create `tests/test_scope.py`:

```python
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
```

Create `tests/test_db.py` (requires a live Postgres from Phase 1's `docker compose up -d postgres` — run `docker compose run --rm liquibase` first if the schema isn't already applied):

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pip install -r scripts/requirements.txt && python3 -m unittest tests.test_scope tests.test_db -v` from the repo root.
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server'` (the package doesn't exist yet).

- [ ] **Step 4: Write the implementation**

Create `mcp_server/__init__.py` (empty file).

Create `mcp_server/scope.py`:

```python
"""Maps a Keycloak token's realm roles to one of our four task-scope roles."""

TASK_SCOPE_ROLES = ("equity_research", "portfolio_risk", "trade_execution", "client_support")


def resolve_scope(claims: dict) -> str | None:
    """Return the first realm role in claims that is one of our task-scope
    roles, or None if the token carries none of them (a Keycloak token's
    realm_access.roles also contains defaults like offline_access/
    uma_authorization/default-roles-<realm>, which are not task scopes)."""
    roles = claims.get("realm_access", {}).get("roles", [])
    for role in roles:
        if role in TASK_SCOPE_ROLES:
            return role
    return None
```

Create `mcp_server/db.py`:

```python
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


def audit(conn, username: str, role: str, kind: str, name: str, decision: str, detail: str) -> None:
    assert role in TASK_SCOPE_ROLES, f"refusing to SET ROLE to unrecognized role: {role!r}"
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute(f"set local role {role}")
            cur.execute("set local app.current_user_id = %s", (username,))
            cur.execute(
                "insert into audit_log (scope, user_id, kind, name, decision, detail) "
                "values (%s, %s, %s, %s, %s, %s)",
                (role, username, kind, name, decision, detail),
            )
            cur.execute("commit")
```

- [ ] **Step 5: Run tests to verify they pass**

Ensure Postgres is up and migrated first: `docker compose up -d postgres && docker compose run --rm liquibase`.

Run: `python3 -m unittest tests.test_scope tests.test_db -v` from the repo root.
Expected: PASS — 7 tests, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add mcp_server/__init__.py mcp_server/scope.py mcp_server/db.py tests/__init__.py tests/test_scope.py tests/test_db.py scripts/requirements.txt
git commit -m "$(cat <<'EOF'
Add mcp_server package core: scope resolution and scoped Postgres access

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `KeycloakTokenVerifier`

**Files:**
- Create: `mcp_server/token_verifier.py`
- Create: `tests/test_token_verifier.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mcp_server.token_verifier.KeycloakTokenVerifier(issuer_url: str, audience: str = "account", jwks_ttl_seconds: int = 300)` — an `mcp.server.auth.provider.TokenVerifier` subclass. Task 5's `server.py` constructs one with the Keycloak realm's issuer URL and passes it to `MCPServer(token_verifier=...)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_token_verifier.py` (requires Task 1's Keycloak running at `http://localhost:8080`):

```python
import asyncio
import unittest

import httpx

from mcp_server.token_verifier import KeycloakTokenVerifier

ISSUER = "http://localhost:8080/realms/financial-mcp"


def get_token(username: str, password: str) -> str:
    resp = httpx.post(
        f"{ISSUER}/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "mcp-server", "username": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


class KeycloakTokenVerifierTest(unittest.TestCase):
    def test_accepts_a_real_token_and_exposes_claims(self):
        token = get_token("alice.research", "alice_dev_only")
        verifier = KeycloakTokenVerifier(ISSUER)
        access_token = asyncio.run(verifier.verify_token(token))
        self.assertIsNotNone(access_token)
        self.assertEqual(access_token.claims["preferred_username"], "alice.research")
        self.assertIn("equity_research", access_token.claims["realm_access"]["roles"])

    def test_rejects_a_tampered_token(self):
        token = get_token("alice.research", "alice_dev_only")
        tampered = token[:-5] + "AAAAA"
        verifier = KeycloakTokenVerifier(ISSUER)
        access_token = asyncio.run(verifier.verify_token(tampered))
        self.assertIsNone(access_token)

    def test_rejects_garbage(self):
        verifier = KeycloakTokenVerifier(ISSUER)
        access_token = asyncio.run(verifier.verify_token("not-a-jwt"))
        self.assertIsNone(access_token)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_token_verifier -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.token_verifier'`.

- [ ] **Step 3: Write the implementation**

Create `mcp_server/token_verifier.py`:

```python
"""Validates Keycloak-issued bearer tokens by fetching and caching the
realm's JWKS and verifying the JWT's signature against it."""
import json
import time
import urllib.request

from jose import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier


class KeycloakTokenVerifier(TokenVerifier):
    def __init__(self, issuer_url: str, audience: str = "account", jwks_ttl_seconds: int = 300):
        self.issuer_url = issuer_url.rstrip("/")
        self.audience = audience
        self.jwks_ttl_seconds = jwks_ttl_seconds
        self._jwks = None
        self._jwks_fetched_at = 0.0

    def _get_jwks(self) -> dict:
        now = time.monotonic()
        if self._jwks is None or (now - self._jwks_fetched_at) > self.jwks_ttl_seconds:
            url = f"{self.issuer_url}/protocol/openid-connect/certs"
            with urllib.request.urlopen(url, timeout=5) as resp:
                self._jwks = json.load(resp)
            self._jwks_fetched_at = now
        return self._jwks

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(
                token,
                self._get_jwks(),
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer_url,
            )
        except Exception:
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("azp", "unknown"),
            scopes=[],
            subject=claims.get("sub"),
            claims=claims,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_token_verifier -v`
Expected: PASS — 3 tests, 0 failures.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/token_verifier.py tests/test_token_verifier.py
git commit -m "$(cat <<'EOF'
Add KeycloakTokenVerifier validating bearer tokens against realm JWKS

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: The 8 tools

**Files:**
- Create: `mcp_server/tools.py`
- Create: `tests/test_tools.py`

**Interfaces:**
- Consumes: `mcp_server.db.get_connection`, `mcp_server.db.scoped_cursor`, `mcp_server.db.audit` (Task 2); `mcp_server.scope.resolve_scope` (Task 2); `mcp.server.auth.middleware.auth_context.get_access_token` (SDK).
- Produces: 8 functions — `list_tables()`, `describe_table(table: str)`, `get_market_snapshot(tickers: list[str] | None = None)`, `get_fundamentals(ticker: str)`, `get_portfolio_exposure(account_id: str)`, `get_account_balance(account_id: str)`, `place_order(account_id: str, ticker: str, side: str, quantity: float)`, `get_audit_log()` — each returning a `dict` shaped `{"decision": "allow", "result": ...}` or `{"decision": "deny"|"error", "detail": str}`. Task 5's `server.py` registers all 8 with `MCPServer.tool()`.

Because these functions call `get_access_token()`, which requires an active MCP request context, they cannot be unit-tested by calling them directly outside a request. `tests/test_tools.py` tests the pure per-tool SQL logic through `mcp_server.db` directly (the same approach `tests/test_db.py` uses) rather than through the tool functions themselves — full request-context testing of the tools happens in Task 6's live, real-client verification.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools.py` (requires live Postgres, same as `tests/test_db.py`; tests the SQL each tool's `fn` closure runs, via `db.scoped_cursor` directly, not through the MCP request-context-dependent wrapper):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_tools -v`
Expected: these specific queries don't depend on `mcp_server/tools.py` existing (they exercise `db.py` directly, already implemented in Task 2) — so this step instead confirms the CURRENT state: run it now, before writing `tools.py`, and expect all 5 to already PASS (Task 2's `db.py` already supports everything these tests check). This is expected — the "RED" step for this task is implicit: these tests validate the SQL patterns `tools.py` is ABOUT to reuse, proven correct before `tools.py` wraps them. Note the pass in your task report as the baseline, then proceed to Step 3.

- [ ] **Step 3: Write the implementation**

Create `mcp_server/tools.py`:

```python
"""The 8 MCP tools. Postgres -- not this code -- enforces the access
boundary; every tool routes its query through mcp_server.db.scoped_cursor
and reports whatever Postgres decided."""
import time

import psycopg2
from mcp.server.auth.middleware.auth_context import get_access_token

from . import db
from .scope import resolve_scope

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
        return {"decision": "deny", "detail": "token carries no recognized task-scope role"}
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
```

- [ ] **Step 4: Run tests to verify they still pass**

Run: `python3 -m unittest tests.test_tools tests.test_db tests.test_scope tests.test_token_verifier -v`
Expected: PASS — all tests from Tasks 2-4, 0 failures (Task 4 didn't change `db.py`'s behavior, only added `tools.py` on top of it, so nothing here should regress).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/tools.py tests/test_tools.py
git commit -m "$(cat <<'EOF'
Add the 8 MCP tools, each routed through Postgres role/RLS enforcement

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Server entry point

**Files:**
- Create: `mcp_server/server.py`

**Interfaces:**
- Consumes: `mcp_server.token_verifier.KeycloakTokenVerifier` (Task 3), all 8 functions from `mcp_server.tools` (Task 4).
- Produces: `mcp_server.server.app` — an ASGI app, servable by `uvicorn mcp_server.server:app`. Task 6's verification script connects to this server over HTTP; no later task imports this module directly.

- [ ] **Step 1: Write the server**

Create `mcp_server/server.py`:

```python
"""Entry point: wires the token verifier and the 8 tools into an MCPServer
and exposes app for uvicorn to serve over Streamable HTTP."""
import os

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from . import tools
from .token_verifier import KeycloakTokenVerifier

ISSUER_URL = os.environ.get("KEYCLOAK_ISSUER_URL", "http://localhost:8080/realms/financial-mcp")
RESOURCE_SERVER_URL = os.environ.get("MCP_RESOURCE_SERVER_URL", "http://localhost:8000")

server = MCPServer(
    "financial-mcp",
    token_verifier=KeycloakTokenVerifier(ISSUER_URL),
    auth=AuthSettings(issuer_url=ISSUER_URL, resource_server_url=RESOURCE_SERVER_URL),
)

server.tool()(tools.list_tables)
server.tool()(tools.describe_table)
server.tool()(tools.get_market_snapshot)
server.tool()(tools.get_fundamentals)
server.tool()(tools.get_portfolio_exposure)
server.tool()(tools.get_account_balance)
server.tool()(tools.place_order)
server.tool()(tools.get_audit_log)

app = server.streamable_http_app()
```

- [ ] **Step 2: Start it and verify the handshake + an authenticated tool call, live**

Ensure Postgres (`docker compose up -d postgres`) and Keycloak (`docker compose up -d keycloak`, from Task 1) are both up.

Run in the background:
```bash
uvicorn mcp_server.server:app --port 8000 --log-level warning &
sleep 2
```

Get a real token and call the handshake:
```bash
TOKEN=$(curl -s -X POST http://localhost:8080/realms/financial-mcp/protocol/openid-connect/token \
  -d "grant_type=password" -d "client_id=mcp-server" -d "username=alice.research" -d "password=alice_dev_only" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -i -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}'
```
Expected: `HTTP/1.1 200 OK`, a `mcp-session-id` response header, and a JSON-RPC result naming `tools`, `resources`, `prompts` capabilities.

Confirm a missing token is rejected:
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}'
```
Expected: `401`.

Stop the server: `kill %1` (or `pkill -f "uvicorn mcp_server.server"`).

- [ ] **Step 3: Commit**

```bash
git add mcp_server/server.py
git commit -m "$(cat <<'EOF'
Add MCP server entry point wiring auth and the 8 tools over Streamable HTTP

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: End-to-end verification script and README

**Files:**
- Create: `scripts/verify_mcp_server.py`
- Modify: `README.md` (add Phase 2 quickstart section)

**Interfaces:**
- Consumes: the running server from Task 5 (`http://localhost:8000/mcp`), Keycloak from Task 1 (`http://localhost:8080`).
- Produces: nothing further tasks depend on (last task of this plan).

- [ ] **Step 1: Write the verification script**

Create `scripts/verify_mcp_server.py`:

```python
"""Independent proof that the real MCP server enforces the same boundary
scripts/verify_scopes.py already proved directly against Postgres --
this time through a real MCP client, real Keycloak tokens, and the real
running server."""
import asyncio
import json
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

KEYCLOAK_URL = os.environ.get("VERIFY_KEYCLOAK_URL", "http://localhost:8080")
REALM = "financial-mcp"
CLIENT_ID = "mcp-server"
SERVER_URL = os.environ.get("VERIFY_SERVER_URL", "http://localhost:8000/mcp")

USERS = {
    "alice.research": "alice_dev_only",
    "bob.risk": "bob_dev_only",
    "carol.trader": "carol_dev_only",
    "dave.support": "dave_dev_only",
}

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def get_token(username: str, password: str) -> str:
    resp = httpx.post(
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": CLIENT_ID, "username": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def call(token: str, name: str, arguments: dict) -> dict:
    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with streamable_http_client(SERVER_URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if result.is_error:
                return {"decision": "transport_error", "detail": result.content[0].text if result.content else ""}
            return json.loads(result.content[0].text)


async def main():
    tokens = {u: get_token(u, p) for u, p in USERS.items()}

    r = await call(tokens["alice.research"], "get_market_snapshot", {})
    check("alice(equity_research) can call get_market_snapshot", r["decision"] == "allow" and len(r["result"]) == 4)

    r = await call(tokens["alice.research"], "get_portfolio_exposure", {"account_id": "ACC-1001"})
    check("alice(equity_research) is denied get_portfolio_exposure (no grant on positions)", r["decision"] == "deny")

    r = await call(tokens["bob.risk"], "get_portfolio_exposure", {"account_id": "ACC-1001"})
    check("bob(portfolio_risk) can call get_portfolio_exposure for his own account", r["decision"] == "allow" and len(r["result"]) == 2)

    r = await call(tokens["bob.risk"], "place_order", {"account_id": "ACC-1001", "ticker": "AAPL", "side": "buy", "quantity": 1})
    check("bob(portfolio_risk) is denied place_order for ACC-1001, which he owns -- wrong role", r["decision"] == "deny")

    r = await call(tokens["carol.trader"], "get_portfolio_exposure", {"account_id": "ACC-1001"})
    check("carol(trade_execution) is denied get_portfolio_exposure for ACC-1001, which she owns -- right account, wrong role", r["decision"] == "deny")

    r = await call(tokens["carol.trader"], "place_order", {"account_id": "ACC-1001", "ticker": "AAPL", "side": "buy", "quantity": 1})
    check("carol(trade_execution) can place an order for ACC-1001, which she owns", r["decision"] == "allow")

    r = await call(tokens["carol.trader"], "place_order", {"account_id": "ACC-1002", "ticker": "AAPL", "side": "buy", "quantity": 1})
    check("carol(trade_execution) is denied placing an order for ACC-1002, which she does NOT own (RLS)", r["decision"] == "deny")

    r = await call(tokens["dave.support"], "get_account_balance", {"account_id": "ACC-1002"})
    check("dave(client_support) can get his own account's balance", r["decision"] == "allow" and r["result"]["balance"] == 12000.0)

    r = await call(tokens["dave.support"], "get_portfolio_exposure", {"account_id": "ACC-1002"})
    check("dave(client_support) is denied get_portfolio_exposure (no grant on positions)", r["decision"] == "deny")

    r = await call(tokens["dave.support"], "place_order", {"account_id": "ACC-1002", "ticker": "AAPL", "side": "buy", "quantity": 1})
    check("dave(client_support) is denied place_order (no grant on orders)", r["decision"] == "deny")

    r = await call(tokens["alice.research"], "get_fundamentals", {"ticker": "NOT-A-TICKER"})
    check("alice(equity_research) gets an 'error' (not 'deny') for an unknown ticker -- bad input, not a permission problem", r["decision"] == "error")

    r = await call(tokens["dave.support"], "get_audit_log", {})
    check("dave(client_support) can read his own audit log through the server", r["decision"] == "allow" and len(r["result"]) >= 1)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it against the live stack**

Ensure Postgres, Keycloak, and the server (`uvicorn mcp_server.server:app --port 8000`) are all running (per Task 5, Step 2 — restart the server if you stopped it there).

Run:
```bash
python3 scripts/verify_mcp_server.py
```
Expected: every line prints `[PASS]`, ending with `All checks passed.`, exit code 0.

- [ ] **Step 3: Add the Phase 2 section to the README**

In `README.md`, update the `## Status` section to read:

```markdown
## Status

Phase 1 (Postgres schema, roles, RLS) and Phase 2 (MCP server + Keycloak auth) are complete and locally verified. The showcase UI and Kubernetes/Argo CD deployment are later phases, not yet built.
```

Add a new section after the Phase 1 quickstart:

```markdown
## Phase 2 quickstart

Requires everything Phase 1 needs, plus Keycloak (already added to `docker-compose.yml`).

```bash
docker compose up -d postgres keycloak
docker compose run --rm liquibase
pip install -r scripts/requirements.txt
uvicorn mcp_server.server:app --port 8000 &
python3 scripts/verify_mcp_server.py
```

`verify_mcp_server.py` proves, through a real MCP client and real Keycloak-issued tokens, that the running server enforces the same task-scope and account-ownership boundaries `verify_scopes.py` already proved directly against Postgres -- this time end-to-end through the actual authentication and tool-call path.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_mcp_server.py README.md
git commit -m "$(cat <<'EOF'
Add end-to-end MCP client verification script and Phase 2 README section

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage:** Task 1 covers the Keycloak realm section; Task 2 covers `db.py`/`scope.py` (the reusable scoped-access pattern the spec calls out); Task 3 covers `token_verifier.py` exactly as specified (JWKS fetch/cache, `verify_token` contract); Task 4 covers all 8 tools with the three-way allow/deny/error decision the spec's "tools.py" section describes; Task 5 covers `server.py`'s wiring; Task 6 covers the verification script and README. No section of the design spec is uncovered.

**Type/name consistency:** `db.get_connection`, `db.scoped_cursor`, `db.audit`, `scope.resolve_scope`, `scope.TASK_SCOPE_ROLES` are used identically across Tasks 2-6. The 8 tool function names and signatures in Task 4 match exactly what Task 5 registers and Task 6 calls by name.

**Placeholder scan:** No TBD/TODO; every code block is complete, runnable content. Task 6, Step 2 includes a concrete conditional note about a specific expected-but-non-obvious outcome (the `get_fundamentals`/`trade_execution` role-vs-ticker-validation ordering) rather than leaving it as an unexplained edge case.
