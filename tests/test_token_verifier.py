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
