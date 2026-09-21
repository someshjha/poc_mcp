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
def auth_callback(request: Request, code: str | None = None, state: str | None = None):
    if code is None:
        return JSONResponse(
            {
                "error": request.query_params.get("error", "login_failed"),
                "detail": request.query_params.get("error_description", "No authorization code received"),
            },
            status_code=400,
        )
    if state != request.session.get("oauth_state"):
        return JSONResponse({"error": "invalid state"}, status_code=400)
    try:
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
    except httpx.HTTPStatusError as exc:
        return JSONResponse({"error": "token_exchange_failed", "detail": exc.response.text}, status_code=502)
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
    except Exception as exc:
        return {"decision": "error", "detail": f"MCP call failed: {exc}"}
    finally:
        await http_client.aclose()


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
