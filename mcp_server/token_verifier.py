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
