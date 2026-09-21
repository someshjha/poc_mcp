# Phase 3 — Showcase UI — design

## Context

Phase 1 proved the database-level claim (Postgres roles + RLS). Phase 2 built a real MCP server authenticating callers via real Keycloak JWTs, proven three independent ways: `scripts/verify_scopes.py` (direct Postgres), `scripts/verify_mcp_server.py` (Python MCP client), and `karate/scoped_access.feature` (Java/Karate, raw HTTP). Phase 3 gives a human a way to see this happen without reading a terminal: a real web UI with real login, calling the real server.

## Everything below was verified directly before this spec was written

- The existing Keycloak client `mcp-server` (used for Phase 2's password-grant testing) already has `standardFlowEnabled: true` by default — no new Keycloak client is needed for Authorization Code login, only a registered `redirectUris` entry, which has been added: `http://localhost:5000/auth/callback` (with matching `webOrigins`).
- The full Authorization Code flow was driven end-to-end via scripted HTTP (form login → redirect with `code` → token exchange) and succeeded: a real access token, ID token, and refresh token were obtained. No PKCE is currently required by this client's configuration.
- `FastAPI` + Starlette's `SessionMiddleware` (signed-cookie sessions, no server-side store) install and initialize cleanly.
- The MCP client mechanics needed to proxy a tool call (real Keycloak token as Bearer, Streamable HTTP, parse the SSE-wrapped, double-JSON-encoded tool result) are already proven working in Phase 2's `scripts/verify_mcp_server.py` — this phase reuses that exact pattern inside a FastAPI route handler instead of a standalone script.

## Goal

A running web app where a person can log in as one of the five demo Keycloak users (including `erin.norole`, who has no task-scope role), see exactly what their identity grants, call tools through the real MCP server, and watch the real audit trail — all through a browser, no terminal required.

## Non-goals

- No new Keycloak client, no PKCE (not required by the current client config; a documented future hardening item, not built now).
- No connection pooling / long-lived MCP session reuse across requests — each tool call does its own connect→initialize→call→close round-trip (see Architecture). Acceptable latency for a demo UI whose point is the auth/scoping story.
- No client-side framework (React/Vue/etc.) — plain HTML/CSS/JS, matching the site's existing mock demo and this repo's own established style.
- No production auth hardening (token refresh on expiry, CSRF protection beyond what Starlette's session cookie already provides, rate limiting) — local demo scope, same posture as Phases 1-2's Non-goals.

## Architecture

One FastAPI app (`ui/app.py`) serving both API routes and static frontend files.

**Routes:**
- `GET /` — serves `ui/static/index.html` (the console SPA). If no valid session, the frontend's own JS redirects to `/login` (simpler than a server-side redirect, since the SPA needs to distinguish "not logged in" from "logged in but a tool call failed" using the same page).
- `GET /login` — redirects (302) to Keycloak's `/protocol/openid-connect/auth` endpoint with `client_id=mcp-server`, `response_type=code`, `redirect_uri=http://localhost:5000/auth/callback`, `scope=openid`, and a random `state` value stored in the session for CSRF protection on callback.
- `GET /auth/callback` — validates `state` against the session, exchanges the `code` for a token via Keycloak's token endpoint, decodes the token's claims locally (no need to re-verify the signature here — the MCP server itself will verify it on every tool call; the UI only reads claims for display), stores `access_token` + `preferred_username` + resolved task scope in the session, redirects to `/`.
- `GET /logout` — clears the session, redirects to `/`.
- `GET /api/me` — returns `{ "username": ..., "scope": ... }` from the session (401 if not logged in). `scope` is resolved the same way `mcp_server.scope.resolve_scope` does server-side (re-implemented client-side in `ui/app.py` from the session's stored claims, since the UI needs this for display before any tool call happens — not a security boundary, just informational).
- `POST /api/tools/{name}` — body is the tool's JSON arguments. Handler: get the session's `access_token`, open a fresh MCP client connection (`streamable_http_client` + `ClientSession`, exactly as `scripts/verify_mcp_server.py` does), call `initialize`, `notifications/initialized`, then `tools/call` with `name`/arguments, parse the SSE response and the double-JSON-encoded tool result, return it as the API response, close the connection. 401 if not logged in.

**Frontend (`ui/static/`):** plain HTML/CSS/JS, dark console theme matching the browser-only mock demo's (`financial-mcp/` in `someshjha.github.io`) established console look, for narrative continuity across mock and real. Six views:
1. **Overview** — who's logged in, their resolved task scope, a "how this works" blurb, a logout link.
2. **Schema** — static display of the 6 Postgres tables (same `SCHEMA` dict already in `mcp_server/tools.py` — the UI calls `list_tables`/`describe_table` through the real server rather than hardcoding it a third time).
3. **Task Scope** — informational only (per the Global Constraint the design spec already established): shows what the *logged-in user's own token* grants, fetched from `/api/me`, not a free picker.
4. **Console** — pick a tool from the catalogue, fill parameters, submit to `/api/tools/{name}`, render the JSON response (allow/deny/error, same three-way decision Phase 2 established).
5. **Resources & Prompts** — static catalogue display (this repo doesn't implement MCP `resources`/`prompts`, only `tools` — Phase 2 never built those primitives, so this view documents that honestly rather than inventing them).
6. **Audit Log** — calls `get_audit_log` through the Console mechanism (or a dedicated button), renders the caller's own audit trail.

## Repo layout additions

```
poc_mcp/
├── ui/
│   ├── app.py
│   ├── requirements.txt        # fastapi, uvicorn, itsdangerous, jinja2, httpx, mcp
│   └── static/
│       ├── index.html
│       ├── styles.css
│       └── app.js
```

## Verification

Extend the existing pattern: a script (`scripts/verify_ui.py`, or reuse Karate) that drives the UI's own HTTP surface — `GET /login` → follow the real Keycloak form login (as already scripted for this spec's own verification) → `GET /auth/callback` → confirm a session cookie is set → `GET /api/me` returns the right scope → `POST /api/tools/get_market_snapshot` returns `allow` → a denied call for a wrong-scope tool returns `deny`. This proves the UI's own auth wiring and proxy logic work, on top of everything Phase 2 already proved about the server itself.

## Open questions / explicitly deferred

- Token refresh: the session's access token expires (5 minutes, matching the realm default) and there's no refresh-token exchange built — a demo session simply needs a fresh `/login` after expiry. Acceptable for a local demo; a real deployment would refresh using the stored `refresh_token`.
- PKCE: not required by the current client config; add if this client config is ever tightened.
