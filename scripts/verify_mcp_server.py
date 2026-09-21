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
    "erin.norole": "erin_dev_only",
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

    r = await call(tokens["erin.norole"], "list_tables", {})
    check(
        "erin(no task-scope role) is denied list_tables with the role-is-None detail",
        r["decision"] == "deny" and r["detail"] == "token carries no recognized task-scope role",
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
