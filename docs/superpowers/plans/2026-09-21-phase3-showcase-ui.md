# Phase 3 — Showcase UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A FastAPI app that is both a real OAuth2 Authorization Code login flow against Keycloak and the browser's proxy to the real MCP server, plus a plain HTML/CSS/JS console frontend, so a person can log in as any of the five demo users and watch the scoped-access boundary work through a browser.

**Architecture:** `ui/app.py` serves static files at `/` and exposes `/login`, `/auth/callback`, `/logout`, `/api/me`, `/api/tools/{name}` — the last of which opens a fresh MCP client connection per call (Streamable HTTP, the same pattern `scripts/verify_mcp_server.py` already uses) authenticated with the logged-in user's own session token. `ui/static/` is a single-page console mirroring the browser-only mock demo's six-view structure.

**Tech Stack:** FastAPI, Starlette `SessionMiddleware`, `httpx`, the `mcp` SDK's client library (already a dependency from Phase 2), vanilla HTML/CSS/JS.

## Global Constraints

- The Keycloak client `mcp-server` already has `standardFlowEnabled: true` and a registered `redirectUris` entry (`http://localhost:5000/auth/callback`) — no new Keycloak client or realm change needed.
- Explicit FastAPI routes registered before a `StaticFiles` mount at `/` correctly take priority over the mount — confirmed directly (an `/api/me` route returns JSON, `/` still serves the static `index.html`, no conflict).
- The MCP client mechanics (`streamable_http_client` + `ClientSession`, SSE response parsing, double-JSON-encoded tool result) work correctly inside a real FastAPI `async def` route handler with no event-loop conflicts — confirmed directly with a live call through a real FastAPI route to the real running MCP server.
- Session claims are decoded (base64) for DISPLAY ONLY in the UI — the UI never re-verifies the JWT signature itself; the real MCP server verifies it on every tool call, same as Phase 2. Decoding without verification here is not a security boundary and must not be treated as one.
- No connection pooling — each `/api/tools/{name}` call does its own connect→initialize→call→close round-trip. This is a deliberate simplification, not a bug to fix.
- No tool-catalogue API endpoint — the 8 tools' names/parameter schemas are hardcoded in `ui/static/app.js`, matching how the browser-only mock demo (`financial-mcp/mock-mcp-server.js` in the `someshjha.github.io` repo) already does this, and must exactly match `mcp_server/tools.py`'s actual 8 function signatures.
- Demo users for manual/scripted testing: `alice.research`/`alice_dev_only` (equity_research), `bob.risk`/`bob_dev_only` (portfolio_risk), `carol.trader`/`carol_dev_only` (trade_execution), `dave.support`/`dave_dev_only` (client_support), `erin.norole`/`erin_dev_only` (no task-scope role).

---

### Task 1: FastAPI backend — auth + tool proxy

**Files:**
- Create: `ui/app.py`
- Create: `ui/requirements.txt`

**Interfaces:**
- Produces: a running ASGI app (`ui.app:app`) servable by `uvicorn ui.app:app --port 5000`, with routes `GET /login`, `GET /auth/callback`, `GET /logout`, `GET /api/me`, `POST /api/tools/{name}`. Task 2's frontend calls these exact paths. Task 3's verification script drives the same paths.

- [ ] **Step 1: Write the requirements file**

Create `ui/requirements.txt`:

```
fastapi>=0.110
uvicorn>=0.30
httpx>=0.27
itsdangerous>=2.2
mcp[cli]>=2.2.0,<3
```

- [ ] **Step 2: Write the backend**

Create `ui/app.py`:

```python
"""FastAPI backend for the scoped financial-data MCP showcase UI.

Serves the static console frontend and acts as the browser's proxy to
the real MCP server: the browser never talks MCP directly (it can't --
Streamable HTTP needs a real HTTP client, not fetch()), it talks to this
app's plain REST API, which in turn is a real MCP client authenticated
with the logged-in user's own Keycloak token.
"""
import base64
import json
import os
import secrets

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.middleware.sessions import SessionMiddleware

KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8080")
REALM = "financial-mcp"
CLIENT_ID = "mcp-server"
REDIRECT_URI = os.environ.get("UI_REDIRECT_URI", "http://localhost:5000/auth/callback")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8000/mcp")
SESSION_SECRET = os.environ.get("UI_SESSION_SECRET", "dev-only-session-secret")

TASK_SCOPE_ROLES = ("equity_research", "portfolio_risk", "trade_execution", "client_support")

app = FastAPI(title="Scoped Financial MCP Showcase")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)


def _resolve_scope(claims: dict) -> str | None:
    roles = claims.get("realm_access", {}).get("roles", [])
    for role in roles:
        if role in TASK_SCOPE_ROLES:
            return role
    return None


def _decode_claims_unverified(jwt_token: str) -> dict:
    """Read claims for display only -- the MCP server verifies the
    signature on every real tool call, so this app doesn't need to."""
    payload = jwt_token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


@app.get("/login")
def login(request: Request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    auth_url = (
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/auth"
        f"?client_id={CLIENT_ID}&response_type=code"
        f"&redirect_uri={REDIRECT_URI}&scope=openid&state={state}"
    )
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
def auth_callback(request: Request, code: str, state: str):
    if state != request.session.get("oauth_state"):
        return JSONResponse({"error": "invalid state"}, status_code=400)
    resp = httpx.post(
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=10,
    )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]
    claims = _decode_claims_unverified(access_token)
    request.session["access_token"] = access_token
    request.session["username"] = claims.get("preferred_username")
    request.session["scope"] = _resolve_scope(claims)
    return RedirectResponse("/")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@app.get("/api/me")
def me(request: Request):
    if "access_token" not in request.session:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    return {
        "username": request.session["username"],
        "scope": request.session["scope"],
    }


@app.post("/api/tools/{name}")
async def call_tool(name: str, request: Request):
    if "access_token" not in request.session:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    token = request.session["access_token"]
    try:
        arguments = await request.json()
    except Exception:
        arguments = {}

    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    try:
        async with streamable_http_client(MCP_SERVER_URL, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                if result.is_error:
                    text = result.content[0].text if result.content else "unknown error"
                    return {"decision": "error", "detail": text}
                return json.loads(result.content[0].text)
    finally:
        await http_client.aclose()


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
```

- [ ] **Step 3: Verify the full auth + proxy flow live, scripted (no browser yet — Task 2 adds the frontend)**

Ensure Postgres, Keycloak, and the MCP server are up (`docker compose up -d postgres keycloak`, migrated; `uvicorn mcp_server.server:app --port 8000 --log-level warning &`, `sleep 2`).

Install deps and start the UI backend:
```bash
pip install -r ui/requirements.txt
uvicorn ui.app:app --port 5000 --log-level warning &
sleep 2
```

Confirm `/api/me` is 401 before login:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5000/api/me
```
Expected: `401`.

Drive the real Authorization Code flow through THIS app's `/login` (not Keycloak directly), using a cookie jar to carry both the UI's session cookie and Keycloak's login-flow cookies:
```bash
COOKIEJAR=$(mktemp)
LOGIN_REDIRECT=$(curl -s -D - -o /dev/null -b "$COOKIEJAR" -c "$COOKIEJAR" http://localhost:5000/login | grep -i "^location:" | cut -d' ' -f2- | tr -d '\r')
echo "redirected to: $LOGIN_REDIRECT"
AUTH_PAGE=$(curl -s -b "$COOKIEJAR" -c "$COOKIEJAR" -L "$LOGIN_REDIRECT")
FORM_ACTION=$(echo "$AUTH_PAGE" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//;s/"$//' | sed 's/\&amp;/\&/g')
CALLBACK_REDIRECT=$(curl -s -D - -o /dev/null -b "$COOKIEJAR" -c "$COOKIEJAR" \
  --data-urlencode "username=alice.research" \
  --data-urlencode "password=alice_dev_only" \
  --data-urlencode "credentialId=" \
  "$FORM_ACTION" | grep -i "^location:" | cut -d' ' -f2- | tr -d '\r')
echo "keycloak redirected to: $CALLBACK_REDIRECT"
curl -s -b "$COOKIEJAR" -c "$COOKIEJAR" -o /dev/null -w "callback status: %{http_code}\n" "$CALLBACK_REDIRECT"
curl -s -b "$COOKIEJAR" http://localhost:5000/api/me
```
Expected: the last line prints `{"username":"alice.research","scope":"equity_research"}`.

Now call a tool through the authenticated session:
```bash
curl -s -b "$COOKIEJAR" -X POST http://localhost:5000/api/tools/get_market_snapshot -H "Content-Type: application/json" -d '{}'
```
Expected: `{"decision":"allow","result":[...4 tickers...]}`.

And a denied one:
```bash
curl -s -b "$COOKIEJAR" -X POST http://localhost:5000/api/tools/get_portfolio_exposure -H "Content-Type: application/json" -d '{"account_id":"ACC-1001"}'
```
Expected: `{"decision":"deny",...}` (alice's role, `equity_research`, has no grant on `positions`).

Stop both background servers: `pkill -f "uvicorn ui.app"`, `pkill -f "uvicorn mcp_server.server"`.

- [ ] **Step 4: Commit**

```bash
git add ui/app.py ui/requirements.txt
git commit -m "$(cat <<'EOF'
Add FastAPI backend: Authorization Code login + MCP tool-call proxy

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Frontend — the console SPA

**Files:**
- Create: `ui/static/index.html`
- Create: `ui/static/styles.css`
- Create: `ui/static/app.js`

**Interfaces:**
- Consumes: `GET /api/me`, `POST /api/tools/{name}` (Task 1).
- Produces: a servable static site at `/` (via Task 1's `StaticFiles` mount). No later task imports this directly; Task 3's verification script may load `/` to confirm it serves non-empty HTML.

- [ ] **Step 1: Write the HTML shell**

Create `ui/static/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Scoped Financial MCP — Real Showcase</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <div class="app-shell">
      <aside class="sidebar" aria-label="Primary navigation">
        <a class="brand" href="#overview">
          <span class="brand-mark" aria-hidden="true">MCP</span>
          <span>Financial<span>MCP</span></span>
        </a>
        <nav class="nav-list">
          <a class="nav-item active" href="#overview" data-view="overview"><span>Overview</span></a>
          <a class="nav-item" href="#schema" data-view="schema"><span>Schema</span></a>
          <a class="nav-item" href="#scope" data-view="scope"><span>Task Scope</span></a>
          <a class="nav-item" href="#console" data-view="console"><span>Console</span></a>
          <a class="nav-item" href="#catalog" data-view="catalog"><span>Resources &amp; Prompts</span></a>
          <a class="nav-item" href="#audit" data-view="audit"><span>Audit Log</span></a>
        </nav>
        <div class="sidebar-footer">
          <span class="environment-chip">REAL SERVER</span>
          <span class="synthetic-label"><i></i> Real Postgres, real Keycloak</span>
        </div>
      </aside>

      <main class="main-content">
        <header class="topbar">
          <div>
            <p class="eyebrow">SCOPED AGENT ACCESS -- LIVE</p>
            <h1 id="page-title">Overview</h1>
          </div>
          <div class="topbar-status">
            <div class="scenario-identity" id="identity-box">
              <span>NOT LOGGED IN</span>
              <strong><a id="login-link" href="/login">Log in</a></strong>
            </div>
          </div>
        </header>

        <div class="content-area">
          <section class="view active" id="view-overview" data-view-panel="overview">
            <div class="section-heading">
              <div><p class="eyebrow">WHAT THIS DEMONSTRATES</p><h2>The real server, not a simulation</h2></div>
            </div>
            <article class="panel overview-copy">
              <p>This page is a real client of a real MCP server, authenticated with a real Keycloak login. When you log in as one of the demo users, every tool call you make in the <strong>Console</strong> tab is a genuine network round trip through Keycloak's issued token, the MCP server's JWT verification, and Postgres's own role/RLS enforcement.</p>
              <p id="overview-status">Log in to begin.</p>
            </article>
          </section>

          <section class="view" id="view-schema" data-view-panel="schema">
            <div class="section-heading"><div><p class="eyebrow">MOCK... NO, REAL POSTGRES SCHEMA</p><h2>Tables (via list_tables / describe_table)</h2></div></div>
            <div class="demo-controls"><button type="button" id="load-schema">Load schema from the server</button></div>
            <div class="schema-grid" id="schema-grid"></div>
          </section>

          <section class="view" id="view-scope" data-view-panel="scope">
            <div class="section-heading"><div><p class="eyebrow">WHO YOU ARE, RIGHT NOW</p><h2>Task scope</h2></div></div>
            <article class="panel" id="scope-panel"><p>Log in to see your task scope.</p></article>
          </section>

          <section class="view" id="view-console" data-view-panel="console">
            <div class="section-heading"><div><p class="eyebrow">CALL A TOOL, FOR REAL</p><h2>Console</h2></div></div>
            <div class="console-layout">
              <form class="panel console-form" id="console-form">
                <label><span>Tool</span><select id="console-tool" name="tool"></select></label>
                <div id="console-params"></div>
                <button class="primary-action" type="submit">Call tool <span>→</span></button>
              </form>
              <aside class="panel console-evidence">
                <div class="panel-header"><h3>Response</h3></div>
                <pre id="console-response"></pre>
              </aside>
            </div>
          </section>

          <section class="view" id="view-catalog" data-view-panel="catalog">
            <div class="section-heading"><div><p class="eyebrow">WHAT'S BUILT SO FAR</p><h2>Resources &amp; prompts</h2></div></div>
            <article class="panel"><p>This server implements MCP <strong>tools</strong> only. <strong>Resources</strong> and <strong>prompts</strong> (separate MCP primitives) are not implemented in this phase -- this view says so honestly rather than inventing them.</p></article>
          </section>

          <section class="view" id="view-audit" data-view-panel="audit">
            <div class="section-heading"><div><p class="eyebrow">REVIEWABLE INDEPENDENTLY</p><h2>Your audit log</h2></div></div>
            <div class="demo-controls"><button type="button" id="load-audit">Load my audit log</button></div>
            <div class="table-scroll">
              <table>
                <thead><tr><th>#</th><th>Time</th><th>Kind</th><th>Name</th><th>Decision</th><th>Detail</th></tr></thead>
                <tbody id="audit-body"></tbody>
              </table>
            </div>
          </section>
        </div>
      </main>
    </div>
    <script src="./app.js"></script>
  </body>
</html>
```

- [ ] **Step 2: Write the stylesheet**

Create `ui/static/styles.css`:

```css
:root {
  color-scheme: dark;
  --ink: #eef5f4;
  --muted: #91a5a2;
  --subtle: #607472;
  --canvas: #07110f;
  --surface: #0c1816;
  --surface-raised: #11211e;
  --surface-soft: #162824;
  --line: #223b36;
  --green: #5ee0a0;
  --green-dark: #173b2c;
  --amber: #f3c56b;
  --amber-dark: #3e321b;
  --red: #ff867c;
  --red-dark: #40221f;
  --sidebar: 224px;
  --radius: 14px;
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* { box-sizing: border-box; }
html { min-width: 320px; background: var(--canvas); }
body { margin: 0; min-height: 100vh; color: var(--ink); background: var(--canvas); }
button, input, select { font: inherit; }
a { color: inherit; }

.app-shell { min-height: 100vh; }

.sidebar {
  position: fixed; inset: 0 auto 0 0; width: var(--sidebar);
  display: flex; flex-direction: column; padding: 24px 14px 18px;
  border-right: 1px solid var(--line); background: var(--surface);
}
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--ink); font-weight: 760; margin-bottom: 26px; }
.brand-mark {
  display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px;
  background: var(--green-dark); color: var(--green); font-size: 11px; font-weight: 800;
}
.brand span span { color: var(--muted); font-weight: 500; }

.nav-list { display: grid; gap: 4px; }
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: 10px;
  color: var(--muted); text-decoration: none; font-size: 13.5px; font-weight: 600;
}
.nav-item:hover { background: var(--surface-soft); color: var(--ink); }
.nav-item.active { background: var(--green-dark); color: var(--green); }

.sidebar-footer { margin-top: auto; display: grid; gap: 8px; }
.environment-chip {
  justify-self: start; padding: 4px 9px; border-radius: 999px; font-size: 10.5px; font-weight: 800;
  background: var(--amber-dark); color: var(--amber);
}
.synthetic-label { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--subtle); }
.synthetic-label i { width: 6px; height: 6px; border-radius: 50%; background: var(--green); }

.main-content { margin-left: var(--sidebar); padding: 22px 28px 60px; max-width: 1180px; }

.topbar { display: flex; justify-content: space-between; align-items: flex-end; gap: 18px; margin-bottom: 22px; flex-wrap: wrap; }
.eyebrow { margin: 0 0 4px; font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: var(--subtle); }
.topbar h1 { margin: 0; font-size: 26px; }
.scenario-identity { display: grid; gap: 2px; text-align: right; }
.scenario-identity span { font-size: 10.5px; letter-spacing: .06em; color: var(--subtle); text-transform: uppercase; }
.scenario-identity strong { font-size: 14px; color: var(--green); }
.scenario-identity a { color: var(--green); text-decoration: none; }

.content-area { position: relative; }
.view { display: none; }
.view.active { display: block; }

.section-heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
.section-heading h2 { margin: 0; font-size: 19px; }

.panel {
  background: var(--surface-raised); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 18px;
}
.panel-header { margin-bottom: 10px; }
.panel-header h3 { margin: 0; font-size: 14px; }

.overview-copy p { margin: 0 0 10px; color: var(--muted); line-height: 1.6; font-size: 14px; }
.overview-copy strong { color: var(--ink); }

.demo-controls { margin-bottom: 14px; }
.demo-controls button {
  background: var(--surface-soft); border: 1px solid var(--line); border-radius: 999px;
  padding: 8px 14px; font-size: 12.5px; color: var(--ink); cursor: pointer;
}

.schema-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
.schema-card h3 { margin: 0 0 10px; font-size: 14px; color: var(--green); font-family: ui-monospace, monospace; }
.schema-card table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.schema-card th { text-align: left; color: var(--subtle); font-weight: 600; padding: 4px 6px; border-bottom: 1px solid var(--line); }
.schema-card td { padding: 4px 6px; border-bottom: 1px solid var(--line); color: var(--muted); font-family: ui-monospace, monospace; }

.console-layout { display: grid; grid-template-columns: minmax(260px, 340px) 1fr; gap: 18px; align-items: start; }
.console-form { display: grid; gap: 12px; }
.console-form label { display: grid; gap: 5px; font-size: 12.5px; color: var(--muted); }
.console-form input, .console-form select {
  background: var(--surface-soft); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 10px; color: var(--ink); font-size: 13.5px;
}
.primary-action {
  justify-self: start; background: var(--green); color: #07160f; border: none; border-radius: 999px;
  padding: 10px 16px; font-size: 13px; font-weight: 760; cursor: pointer;
}
.console-evidence pre {
  margin: 0; background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px; font-size: 12px; overflow: auto; max-height: 400px; color: var(--muted);
  font-family: ui-monospace, monospace; white-space: pre-wrap; word-break: break-word;
}

.table-scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
thead th {
  text-align: left; padding: 10px 12px; background: var(--surface-soft); color: var(--subtle);
  font-weight: 600; text-transform: uppercase; font-size: 10.5px; border-bottom: 1px solid var(--line);
}
tbody td { padding: 8px 12px; border-bottom: 1px solid var(--line); color: var(--muted); }
.decision-badge { padding: 2px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 700; text-transform: uppercase; }
tr.decision-allow .decision-badge { background: var(--green-dark); color: var(--green); }
tr.decision-deny .decision-badge { background: var(--red-dark); color: var(--red); }
tr.decision-error .decision-badge { background: var(--amber-dark); color: var(--amber); }

@media (max-width: 860px) {
  .sidebar { position: static; width: auto; border-right: none; border-bottom: 1px solid var(--line); display: flex; flex-direction: row; flex-wrap: wrap; align-items: center; row-gap: 10px; }
  .brand { margin-bottom: 0; margin-right: 8px; }
  .nav-list { display: flex; flex-direction: row; flex-wrap: wrap; gap: 4px; }
  .sidebar-footer { margin-top: 0; margin-left: auto; display: flex; flex-direction: row; align-items: center; gap: 8px; }
  .main-content { margin-left: 0; padding: 18px 16px 40px; }
  .console-layout { grid-template-columns: 1fr; }
}
```

- [ ] **Step 3: Write the app logic**

Create `ui/static/app.js`:

```js
const TOOLS = [
  { name: "list_tables", params: [] },
  { name: "describe_table", params: [{ name: "table", type: "string", required: true }] },
  { name: "get_market_snapshot", params: [{ name: "tickers", type: "string[]", required: false }] },
  { name: "get_fundamentals", params: [{ name: "ticker", type: "string", required: true }] },
  { name: "get_portfolio_exposure", params: [{ name: "account_id", type: "string", required: true }] },
  { name: "get_account_balance", params: [{ name: "account_id", type: "string", required: true }] },
  {
    name: "place_order",
    params: [
      { name: "account_id", type: "string", required: true },
      { name: "ticker", type: "string", required: true },
      { name: "side", type: "string", required: true },
      { name: "quantity", type: "number", required: true },
    ],
  },
  { name: "get_audit_log", params: [] },
];

const views = document.querySelectorAll(".view");
const navItems = document.querySelectorAll(".nav-item[data-view]");
const pageTitle = document.getElementById("page-title");

const VIEW_TITLES = { overview: "Overview", schema: "Schema", scope: "Task Scope", console: "Console", catalog: "Resources & Prompts", audit: "Audit Log" };

function showView(viewId) {
  views.forEach((v) => v.classList.toggle("active", v.dataset.viewPanel === viewId));
  navItems.forEach((n) => n.classList.toggle("active", n.dataset.view === viewId));
  pageTitle.textContent = VIEW_TITLES[viewId] ?? viewId;
}

navItems.forEach((item) => {
  item.addEventListener("click", (e) => {
    e.preventDefault();
    showView(item.dataset.view);
  });
});

async function callTool(name, args) {
  const resp = await fetch(`/api/tools/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  return resp.json();
}

async function loadIdentity() {
  const resp = await fetch("/api/me");
  const identityBox = document.getElementById("identity-box");
  const overviewStatus = document.getElementById("overview-status");
  const scopePanel = document.getElementById("scope-panel");
  if (resp.status === 401) {
    identityBox.innerHTML = `<span>NOT LOGGED IN</span><strong><a id="login-link" href="/login">Log in</a></strong>`;
    overviewStatus.textContent = "Log in to begin.";
    scopePanel.innerHTML = "<p>Log in to see your task scope.</p>";
    return null;
  }
  const me = await resp.json();
  identityBox.innerHTML = `<span>LOGGED IN AS</span><strong>${me.username} (${me.scope ?? "no task-scope role"}) &middot; <a href="/logout">Log out</a></strong>`;
  overviewStatus.textContent = `You are ${me.username}, with task scope "${me.scope ?? "none"}". Open Console to call a tool.`;
  scopePanel.innerHTML = me.scope
    ? `<p>Your token grants the <strong>${me.scope}</strong> task scope. Try tools in the Console -- some will succeed, some will be denied by the server/Postgres, based on this scope.</p>`
    : `<p>Your token carries <strong>no recognized task-scope role</strong>. Every tool call will be denied -- this demonstrates the server's fail-closed behavior for an unrecognized identity.</p>`;
  return me;
}

function renderConsoleForm() {
  const toolSelect = document.getElementById("console-tool");
  const paramsHost = document.getElementById("console-params");
  toolSelect.innerHTML = TOOLS.map((t) => `<option value="${t.name}">${t.name}</option>`).join("");

  function renderParamFields() {
    const def = TOOLS.find((t) => t.name === toolSelect.value);
    paramsHost.innerHTML = def.params
      .map(
        (p) =>
          `<label><span>${p.name}${p.required ? "" : " (optional)"}</span><input name="${p.name}" type="${p.type === "number" ? "number" : "text"}" ${p.required ? "required" : ""} /></label>`
      )
      .join("");
  }
  toolSelect.onchange = renderParamFields;
  renderParamFields();
}

document.getElementById("console-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const toolName = document.getElementById("console-tool").value;
  const def = TOOLS.find((t) => t.name === toolName);
  const args = {};
  def.params.forEach((p) => {
    const input = e.target.elements[p.name];
    if (!input || input.value === "") return;
    if (p.type === "number") args[p.name] = Number(input.value);
    else if (p.type === "string[]") args[p.name] = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    else args[p.name] = input.value;
  });
  const result = await callTool(toolName, args);
  document.getElementById("console-response").textContent = JSON.stringify(result, null, 2);
});

document.getElementById("load-schema").addEventListener("click", async () => {
  const listResult = await callTool("list_tables", {});
  if (listResult.decision !== "allow") {
    document.getElementById("schema-grid").innerHTML = `<p>Could not load schema: ${JSON.stringify(listResult)}</p>`;
    return;
  }
  const cards = await Promise.all(
    listResult.result.map(async (table) => {
      const described = await callTool("describe_table", { table });
      if (described.decision !== "allow") return "";
      const columns = described.result.columns;
      return `<article class="panel schema-card"><h3>${table}</h3><table><thead><tr><th>Column</th></tr></thead><tbody>${columns.map((c) => `<tr><td>${c}</td></tr>`).join("")}</tbody></table></article>`;
    })
  );
  document.getElementById("schema-grid").innerHTML = cards.join("");
});

document.getElementById("load-audit").addEventListener("click", async () => {
  const result = await callTool("get_audit_log", {});
  const body = document.getElementById("audit-body");
  if (result.decision !== "allow") {
    body.innerHTML = `<tr><td colspan="6">${JSON.stringify(result)}</td></tr>`;
    return;
  }
  body.innerHTML =
    result.result
      .slice()
      .reverse()
      .map(
        (e) =>
          `<tr class="decision-${e.decision}"><td>${e.id}</td><td>${e.at}</td><td>${e.kind}</td><td>${e.name}</td><td><span class="decision-badge">${e.decision}</span></td><td>${e.detail ?? ""}</td></tr>`
      )
      .join("") || `<tr><td colspan="6">No calls yet.</td></tr>`;
});

renderConsoleForm();
loadIdentity();
showView("overview");
```

- [ ] **Step 4: Verify in the browser**

Ensure the full stack is up: Postgres, Keycloak, `uvicorn mcp_server.server:app --port 8000 --log-level warning &`, `uvicorn ui.app:app --port 5000 --log-level warning &` (each with `sleep 2` after starting).

In the Browser pane:
1. Navigate to `http://localhost:5000/`. Confirm the Overview view renders with "NOT LOGGED IN" and a "Log in" link.
2. Click "Log in", complete the Keycloak login form as `alice.research` / `alice_dev_only`. Confirm you're redirected back to `http://localhost:5000/` and the topbar now shows "LOGGED IN AS alice.research (equity_research)".
3. Click **Console**, select `get_market_snapshot`, submit with no params. Confirm the response pane shows `"decision": "allow"` and 4 tickers.
4. Select `get_portfolio_exposure`, fill `account_id=ACC-1001`, submit. Confirm `"decision": "deny"`.
5. Click **Schema**, click "Load schema from the server", confirm 6 table cards render with real column names fetched through `list_tables`/`describe_table`.
6. Click **Audit Log**, click "Load my audit log", confirm the calls just made appear, most recent first, with correct decision badges.
7. Click "Log out", confirm the topbar reverts to "NOT LOGGED IN".
8. Check the Browser pane console for errors (`read_console_messages`, `onlyErrors: true`) — expect none.

Stop both background servers when done: `pkill -f "uvicorn ui.app"`, `pkill -f "uvicorn mcp_server.server"`.

- [ ] **Step 5: Commit**

```bash
git add ui/static/index.html ui/static/styles.css ui/static/app.js
git commit -m "$(cat <<'EOF'
Add the console SPA frontend for the real MCP showcase

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: End-to-end verification script and README

**Files:**
- Create: `scripts/verify_ui.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the running UI (`http://localhost:5000`), the running MCP server, Keycloak.
- Produces: nothing further tasks depend on (last task of this plan).

- [ ] **Step 1: Write the verification script**

Create `scripts/verify_ui.py`:

```python
"""Independent proof that the showcase UI's own auth wiring and tool
proxy work -- drives the UI's real HTTP surface (not the MCP server
directly), including a real Keycloak form login through the UI's own
/login redirect, exactly as a browser would."""
import re

import httpx

BASE = "http://localhost:5000"
FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def login(client: httpx.Client, username: str, password: str) -> None:
    resp = client.get(f"{BASE}/login")
    auth_page = client.get(resp.headers["location"])
    match = re.search(r'action="([^"]+)"', auth_page.text)
    form_action = match.group(1).replace("&amp;", "&")
    callback = client.post(
        form_action,
        data={"username": username, "password": password, "credentialId": ""},
    )
    client.get(callback.headers["location"])


def main():
    with httpx.Client(follow_redirects=False) as client:
        resp = client.get(f"{BASE}/api/me")
        check("not logged in -> 401", resp.status_code == 401)

        login(client, "alice.research", "alice_dev_only")
        me = client.get(f"{BASE}/api/me").json()
        check("logged in as alice with equity_research scope", me == {"username": "alice.research", "scope": "equity_research"})

        allow_resp = client.post(f"{BASE}/api/tools/get_market_snapshot", json={}).json()
        check("alice can call get_market_snapshot through the UI", allow_resp.get("decision") == "allow" and len(allow_resp.get("result", [])) == 4)

        deny_resp = client.post(f"{BASE}/api/tools/get_portfolio_exposure", json={"account_id": "ACC-1001"}).json()
        check("alice is denied get_portfolio_exposure through the UI", deny_resp.get("decision") == "deny")

        index_resp = client.get(f"{BASE}/")
        check("/ serves non-empty HTML", index_resp.status_code == 200 and len(index_resp.text) > 100)

        client.get(f"{BASE}/logout")
        after_logout = client.get(f"{BASE}/api/me")
        check("logged out -> 401 again", after_logout.status_code == 401)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the live stack**

Ensure Postgres, Keycloak, the MCP server, and the UI (`uvicorn ui.app:app --port 5000`) are all running.

Run:
```bash
python3 scripts/verify_ui.py
```
Expected: every line prints `[PASS]`, ending with `All checks passed.`, exit code 0.

- [ ] **Step 3: Update the README**

Update the `## Status` line to:

```markdown
Phase 1 (Postgres schema, roles, RLS), Phase 2 (MCP server + Keycloak auth), and Phase 3 (showcase UI) are complete and locally verified. Kubernetes/Argo CD deployment is a later phase, not yet built.
```

Add a new section after the Karate section:

```markdown
## Phase 3 quickstart

Requires everything Phase 2 needs, plus the UI's own dependencies.

```bash
docker compose up -d postgres keycloak
docker compose run --rm liquibase
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/realms/financial-mcp)" = "200" ]; do sleep 1; done
pip install -r scripts/requirements.txt -r ui/requirements.txt
uvicorn mcp_server.server:app --port 8000 --log-level warning &
sleep 2
uvicorn ui.app:app --port 5000 --log-level warning &
sleep 2
python3 scripts/verify_ui.py
```

Then open `http://localhost:5000/` in a browser and log in as any of the five demo users (`alice.research`/`alice_dev_only`, `bob.risk`/`bob_dev_only`, `carol.trader`/`carol_dev_only`, `dave.support`/`dave_dev_only`, `erin.norole`/`erin_dev_only`) to see the scoped-access boundary work live, through a real login.

Stop both background servers when done: `pkill -f "uvicorn mcp_server.server"`, `pkill -f "uvicorn ui.app"`.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_ui.py README.md
git commit -m "$(cat <<'EOF'
Add end-to-end UI verification script and Phase 3 README section

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage:** Task 1 covers all 5 backend routes from the design spec's Architecture section exactly. Task 2 covers all 6 frontend views. Task 3 covers the Verification section. No spec section is uncovered.

**Type/name consistency:** Route paths (`/login`, `/auth/callback`, `/logout`, `/api/me`, `/api/tools/{name}`) are identical across Tasks 1-3. The 8 tool names/param schemas in `ui/static/app.js` match `mcp_server/tools.py`'s actual function signatures exactly (cross-checked against Phase 2's already-reviewed code).

**Placeholder scan:** No TBD/TODO; every code block is complete, runnable content.
