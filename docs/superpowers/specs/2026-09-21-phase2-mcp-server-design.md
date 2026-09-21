# Phase 2 — MCP server — design

## Context

Phase 1 (complete, on this branch) proved the database-level claim: Postgres itself — via 4 task-scope roles and Row-Level Security keyed on `account_owners` — enforces both the capability boundary and the identity boundary, independent of any application code. `scripts/verify_scopes.py` demonstrates this by connecting to Postgres directly and impersonating each demo user via `SET LOCAL ROLE` + `SET LOCAL app.current_user_id`.

Phase 2 builds the actual MCP server: a real, network-reachable server implementing the mock demo's 8-tool catalogue, authenticating callers against a real Keycloak instance, and doing exactly what `verify_scopes.py` does by hand — but per real request, driven by a real JWT's claims instead of a hardcoded test loop.

This phase is still local (no Docker container for the server itself, no Kubernetes) — Keycloak joins Postgres in `docker-compose.yml` as an external dependency, but the MCP server runs as a plain host process. Containerizing the server is Phase 4.

## Everything below was verified directly against the actual installed toolchain before this spec was written — not assumed from prior knowledge

- The `mcp` PyPI package currently installs at **major version 2.x** (2.2.0 observed), where `FastMCP` (the commonly-documented v1 API) was renamed to **`MCPServer`**, importable from `mcp.server.mcpserver`. Any Phase 2 code referencing `mcp.server.fastmcp.FastMCP` is targeting the wrong API version and will fail with `ModuleNotFoundError` and a migration-guide pointer.
- `MCPServer` accepts a `token_verifier: TokenVerifier | None` and `auth: AuthSettings | None` constructor argument. `TokenVerifier` is a `Protocol` with one method: `async def verify_token(self, token: str) -> AccessToken | None`. `AccessToken` is a pydantic model with `token`, `client_id`, `scopes`, `expires_at`, `resource`, `subject`, and `claims: dict[str, Any] | None`.
- Inside a `@server.tool()`-decorated function, the current request's `AccessToken` is retrieved via `get_access_token()` from `mcp.server.auth.middleware.auth_context` — confirmed live: a tool handler correctly read back `subject` and a custom `claims` entry set by a test `TokenVerifier`.
- `AuthSettings` requires `issuer_url` and `resource_server_url`. Confirmed live: requests with a missing or invalid Bearer token are rejected with `401` by the SDK's own auth middleware before reaching any tool code — the server enforces the Bearer requirement itself, not application code.
- `server.streamable_http_app()` returns an ASGI app servable by `uvicorn`; the `/mcp` endpoint handles the full JSON-RPC handshake (`initialize` → `notifications/initialized` → `tools/call`) over Streamable HTTP with a session-id header, confirmed with a live handshake and tool call through a real HTTP round trip.
- **Keycloak's declarative user profile (default-enabled since Keycloak 21+) silently blocks password-grant login with the generic error `"Account is not fully set up"` unless a user has `email`, `firstName`, and `lastName` set, in addition to `requiredActions: []`.** This is not documented anywhere obvious and was discovered by trial: a user created with only a username and password fails to log in with no actionable error until these profile fields are filled in. Every demo user in `keycloak/realm-export.json` must include them.
- `--import-realm` (realm JSON mounted into `/opt/keycloak/data/import/realm.json`, `start-dev --import-realm`) correctly imports a hand-authored realm containing `roles.realm`, `clients`, and `users` (with `credentials` and `realmRoles` inline) — confirmed live: a fresh Keycloak container booted with only this flag and file produced a realm where the demo user could immediately obtain a working, JWKS-verifiable access token, with zero admin-API calls.
- `docker run --network host` does not behave as expected on this host's Docker setup (confirmed: a container with `--network host` never became reachable on its mapped port; the identical container with `-p HOST:CONTAINER` worked immediately). **`docker-compose.yml` must continue using standard port publishing and Compose's own service-name DNS between containers, never `--network host`,** consistent with how Phase 1's `postgres`/`liquibase` services already work.
- `python-jose[cryptography]` correctly validates a real Keycloak-issued RS256 JWT against the realm's live JWKS endpoint (`{issuer}/protocol/openid-connect/certs`), extracting `sub`, `preferred_username`, and `realm_access.roles`, and correctly rejects a tampered token (confirmed: flipping the last few characters of a valid token's signature raises `JWTError` on verification).

## Goal

A running MCP server, reachable over Streamable HTTP, that:
- Authenticates every request via a real Keycloak-issued JWT (rejecting missing/invalid/tampered tokens at the transport layer, before any tool runs).
- Exposes the same 8 tools as the mock demo (`list_tables`, `describe_table`, `get_market_snapshot`, `get_fundamentals`, `get_portfolio_exposure`, `get_account_balance`, `place_order`, `get_audit_log`).
- For every tool call, derives the caller's identity (`preferred_username`) and task-scope role (from `realm_access.roles`) from the verified JWT, and executes the query against Phase 1's Postgres schema using the exact same `SET LOCAL ROLE` + `SET LOCAL app.current_user_id` pattern `verify_scopes.py` already proved — so the database, not the server's own logic, is still what actually enforces the boundary.
- Writes every call (allowed or denied) to `audit_log`.

## Non-goals

- No UI (Phase 3). Tokens for testing are obtained directly via Keycloak's password grant (the `mcp-server` client is a public client with direct-access-grants enabled specifically to make this possible without a browser).
- No containerization of the MCP server itself (Phase 4) — it runs as a plain `python3` process on the host for this phase.
- No Kubernetes or Argo CD (Phases 4-5).
- No token refresh/session management beyond what a single short-lived access token's natural expiry provides — matches the real-implementation spec's stated scope.
- No new RLS or grant changes — Phase 1's schema is consumed as-is.

## Keycloak realm

Added to `docker-compose.yml` as a new service (`quay.io/keycloak/keycloak:26.0`, command `start-dev --import-realm`, admin bootstrap credentials via env vars, realm JSON mounted read-only into `/opt/keycloak/data/import/realm.json`).

`keycloak/realm-export.json` defines:
- Realm `financial-mcp`.
- Client `mcp-server`: `publicClient: true`, `directAccessGrantsEnabled: true` — sufficient for password-grant login used by the verification script; Phase 3's UI will use a proper Authorization Code flow against the same client (or a second, browser-facing client) later.
- 4 realm roles, named identically to the Postgres roles: `equity_research`, `portfolio_risk`, `trade_execution`, `client_support`.
- 4 users, matching Phase 1's demo identities exactly (`alice.research`, `bob.risk`, `carol.trader`, `dave.support`), each with `enabled: true`, `emailVerified: true`, an `email`, `firstName`, `lastName`, `requiredActions: []`, a `credentials` entry with a fixed dev password, and a `realmRoles` entry naming their one task-scope role — matching Phase 1's Global Constraints table (`alice.research`→`equity_research`, `bob.risk`→`portfolio_risk`, `carol.trader`→`trade_execution`, `dave.support`→`client_support`).

Account *ownership* (who owns `ACC-1001`, etc.) remains entirely a Postgres concept (`account_owners`, already seeded in Phase 1) — Keycloak only carries task-scope role and identity (`preferred_username`), never account ownership. The server never needs to ask Keycloak "what does this user own"; it asks Postgres, via RLS, exactly as `verify_scopes.py` already does.

## MCP server

`mcp_server/` package:
- `token_verifier.py` — `KeycloakTokenVerifier(TokenVerifier)`: on construction, takes the realm's issuer URL; `verify_token` decodes the JWT header to get `kid`, fetches (and caches) the realm's JWKS, verifies the signature and standard claims (`iss`, `exp`) via `python-jose`, and on success returns an `AccessToken(token=token, client_id=claims["azp"], scopes=[], subject=claims["sub"], claims=claims)`. On any verification failure, returns `None` (the SDK's own middleware then responds `401` — confirmed behavior).
- `db.py` — a small helper wrapping `psycopg2`: given a `(username, role)` pair, opens a transaction, runs `SET LOCAL ROLE`/`SET LOCAL app.current_user_id`, and yields a cursor; a second helper writes one `audit_log` row. This is the same pattern as `scripts/verify_scopes.py`'s `run_as`/`audit` functions, extracted into a reusable module both the server and future verification scripts import, rather than duplicated.
- `scope.py` — one function, `resolve_scope(claims: dict) -> str | None`, that scans `claims["realm_access"]["roles"]` and returns the first entry that is one of the 4 known task-scope role names (a Keycloak user's roles list also contains defaults like `offline_access`/`uma_authorization`, which must be ignored), or `None` if none match (the server then denies with a clear error rather than guessing).
- `tools.py` — the 8 tool implementations, each: calls `get_access_token()`, calls `resolve_scope()`, calls `db.py`'s helper to run its query and log the outcome, and returns the result (or a structured error on denial/failure — mirroring the mock's `AuditEntry`-shaped response so the eventual showcase UI, Phase 3, can render it the same way the mock console does).
- `server.py` — constructs the `MCPServer` with the `KeycloakTokenVerifier` and `AuthSettings`, registers the 8 tools from `tools.py`, and exposes `app = server.streamable_http_app()` for `uvicorn` to serve.

## Verification

`scripts/verify_mcp_server.py` — a real MCP client (using the `mcp` SDK's client library, Streamable HTTP transport) that, for each of the 4 demo users:
1. Requests a real access token from Keycloak's token endpoint (password grant, same mechanism proven above).
2. Connects to the running MCP server with that token as the Bearer credential.
3. Calls the same allow/deny scenarios `verify_scopes.py` already proved at the database layer (the `bob.risk`/`carol.trader` overlapping-ownership-of-`ACC-1001` case included), asserting the server's responses match.
4. Confirms `get_audit_log` (called through the server itself, not a direct DB connection) shows the calls just made.

This is the same "independent proof" pattern as Phase 1, one layer up: Phase 1's script proves the database enforces the boundary; this script proves the server correctly *uses* that enforcement rather than bypassing or reimplementing it insecurely in application code.

## Repo layout additions

```
poc_mcp/
├── keycloak/
│   └── realm-export.json
├── mcp_server/
│   ├── __init__.py
│   ├── token_verifier.py
│   ├── db.py
│   ├── scope.py
│   ├── tools.py
│   └── server.py
├── scripts/
│   └── verify_mcp_server.py
```

`docker-compose.yml` gains the `keycloak` service. `README.md` gains a Phase 2 quickstart section.

## Open questions / explicitly deferred

- Token refresh mid-session — out of scope; a token's natural expiry (5 minutes by default in this realm) is the session lifetime for this phase, matching the real-implementation spec.
- Rate limiting / abuse protection on the MCP server — out of scope for a local demo.
- The `mcp-server` client's `directAccessGrantsEnabled` (password grant) is intentionally a development convenience for scripted testing; Phase 3's UI-driven login should use Authorization Code instead, and this spec does not claim password grant is appropriate beyond local verification.
