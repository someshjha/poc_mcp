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

    def test_jwks_base_url_defaults_to_issuer_url(self):
        verifier = KeycloakTokenVerifier(issuer_url="http://issuer.example/realms/x")
        self.assertEqual(verifier.jwks_base_url, "http://issuer.example/realms/x")

    def test_jwks_base_url_overridable_independent_of_issuer(self):
        verifier = KeycloakTokenVerifier(
            issuer_url="http://localhost:8080/realms/financial-mcp",
            jwks_base_url="http://keycloak.financial-mcp.svc.cluster.local:8080/realms/financial-mcp",
        )
        self.assertEqual(verifier.issuer_url, "http://localhost:8080/realms/financial-mcp")
        self.assertEqual(
            verifier.jwks_base_url,
            "http://keycloak.financial-mcp.svc.cluster.local:8080/realms/financial-mcp",
        )

    def test_verify_token_fetches_jwks_from_jwks_base_url_not_issuer_url(self):
        # This file's existing tests exercise KeycloakTokenVerifier against a
        # real, running Keycloak (no urllib.request.urlopen mocking is used
        # anywhere in this file), so we follow that same integration style
        # here: issuer_url is set to the real issuer (so the `iss` claim
        # check still passes), but jwks_base_url is pointed at an
        # unreachable host. If _get_jwks() still used issuer_url internally
        # (the pre-decoupling bug), the fetch would succeed against the real
        # Keycloak and verification would succeed; since it correctly uses
        # jwks_base_url, the fetch fails and verify_token returns None.
        token = get_token("alice.research", "alice_dev_only")
        verifier = KeycloakTokenVerifier(
            issuer_url=ISSUER,
            jwks_base_url="http://localhost:1/realms/financial-mcp",
        )
        access_token = asyncio.run(verifier.verify_token(token))
        self.assertIsNone(access_token)


if __name__ == "__main__":
    unittest.main()
