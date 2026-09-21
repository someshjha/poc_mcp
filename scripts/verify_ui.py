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


def _extract_cookies(resp: httpx.Response) -> dict:
    """Manually parse Set-Cookie headers into name->value pairs, bypassing
    http.cookiejar entirely -- it mishandles single-label hosts like
    'localhost' (stores them under a synthetic 'localhost.local' domain
    that then fails to match on the next request), and Keycloak's interim
    login-flow cookies are marked Secure even over plain http://, which a
    spec-correct non-browser cookie jar refuses to send back (only real
    browsers get a documented exemption for localhost). Carrying cookies
    as a plain dict sidesteps both issues."""
    cookies = {}
    for k, v in resp.headers.multi_items():
        if k.lower() == "set-cookie":
            name, _, rest = v.partition("=")
            value = rest.split(";", 1)[0]
            cookies[name.strip()] = value.strip()
    return cookies


def login(client: httpx.Client, username: str, password: str) -> dict:
    """Returns the accumulated cookie dict after a full login, for use on
    subsequent requests via the `cookies=` parameter."""
    cookie_jar: dict = {}

    resp = client.get(f"{BASE}/login")
    cookie_jar.update(_extract_cookies(resp))

    auth_page = client.get(resp.headers["location"], cookies=cookie_jar)
    cookie_jar.update(_extract_cookies(auth_page))

    match = re.search(r'action="([^"]+)"', auth_page.text)
    form_action = match.group(1).replace("&amp;", "&")

    callback = client.post(
        form_action,
        data={"username": username, "password": password, "credentialId": ""},
        cookies=cookie_jar,
    )
    cookie_jar.update(_extract_cookies(callback))

    cb2 = client.get(callback.headers["location"], cookies=cookie_jar)
    cookie_jar.update(_extract_cookies(cb2))

    return cookie_jar


def main():
    with httpx.Client(follow_redirects=False) as client:
        resp = client.get(f"{BASE}/api/me")
        check("not logged in -> 401", resp.status_code == 401)

        cookie_jar = login(client, "alice.research", "alice_dev_only")
        me = client.get(f"{BASE}/api/me", cookies=cookie_jar).json()
        check("logged in as alice with equity_research scope", me == {"username": "alice.research", "scope": "equity_research"})

        allow_resp = client.post(f"{BASE}/api/tools/get_market_snapshot", json={}, cookies=cookie_jar).json()
        check("alice can call get_market_snapshot through the UI", allow_resp.get("decision") == "allow" and len(allow_resp.get("result", [])) == 4)

        deny_resp = client.post(f"{BASE}/api/tools/get_portfolio_exposure", json={"account_id": "ACC-1001"}, cookies=cookie_jar).json()
        check("alice is denied get_portfolio_exposure through the UI", deny_resp.get("decision") == "deny")

        index_resp = client.get(f"{BASE}/", cookies=cookie_jar)
        check("/ serves non-empty HTML", index_resp.status_code == 200 and len(index_resp.text) > 100)

        logout_resp = client.get(f"{BASE}/logout", cookies=cookie_jar)
        cookie_jar.update(_extract_cookies(logout_resp))
        after_logout = client.get(f"{BASE}/api/me", cookies=cookie_jar)
        check("logged out -> 401 again", after_logout.status_code == 401)

        unauth_post = client.post(f"{BASE}/api/tools/place_order", json={})
        check("unauthenticated POST /api/tools/place_order -> 401", unauth_post.status_code == 401)

        erin_cookies = login(client, "erin.norole", "erin_dev_only")
        erin_deny = client.post(f"{BASE}/api/tools/list_tables", json={}, cookies=erin_cookies).json()
        check("erin.norole is denied list_tables through the UI", erin_deny.get("decision") == "deny")

        carol_cookies = login(client, "carol.trader", "carol_dev_only")
        carol_order = client.post(
            f"{BASE}/api/tools/place_order",
            json={"account_id": "ACC-1001", "ticker": "AAPL", "side": "buy", "quantity": 1},
            cookies=carol_cookies,
        ).json()
        check("carol.trader can place_order through the UI", carol_order.get("decision") == "allow")

    with httpx.Client(follow_redirects=False) as bogus_client:
        bogus_callback = bogus_client.get(
            f"{BASE}/auth/callback",
            params={"code": "whatever", "state": "bogus-value-that-does-not-match"},
        )
        check("callback with bogus state -> 400", bogus_callback.status_code == 400)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
