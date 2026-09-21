"""Entry point: wires the token verifier and the 8 tools into an MCPServer
and exposes app for uvicorn to serve over Streamable HTTP."""
import os

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import tools
from .token_verifier import KeycloakTokenVerifier

ISSUER_URL = os.environ.get("KEYCLOAK_ISSUER_URL", "http://localhost:8080/realms/financial-mcp")
JWKS_BASE_URL = os.environ.get("KEYCLOAK_JWKS_URL")  # None -> defaults to ISSUER_URL
RESOURCE_SERVER_URL = os.environ.get("MCP_RESOURCE_SERVER_URL", "http://localhost:8000")

# streamable_http_app()'s default DNS-rebinding protection only allowlists
# localhost/127.0.0.1/::1 Host headers, which rejects callers that reach this
# server via an in-cluster Service DNS name (e.g. the ui pod calling
# "mcp-server.financial-mcp.svc.cluster.local:8000") with a 421 Misdirected
# Request. MCP_ALLOWED_HOSTS lets the k8s manifest add that hostname to the
# allowlist while leaving the docker-compose/host default (localhost only)
# unchanged.
_EXTRA_ALLOWED_HOSTS = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
TRANSPORT_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *_EXTRA_ALLOWED_HOSTS],
    allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
)

server = MCPServer(
    "financial-mcp",
    token_verifier=KeycloakTokenVerifier(ISSUER_URL, jwks_base_url=JWKS_BASE_URL),
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

app = server.streamable_http_app(transport_security=TRANSPORT_SECURITY)
