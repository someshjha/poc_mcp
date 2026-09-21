"""Maps a Keycloak token's realm roles to one of our four task-scope roles."""

TASK_SCOPE_ROLES = ("equity_research", "portfolio_risk", "trade_execution", "client_support")


def resolve_scope(claims: dict) -> str | None:
    """Return the first realm role in claims that is one of our task-scope
    roles, or None if the token carries none of them (a Keycloak token's
    realm_access.roles also contains defaults like offline_access/
    uma_authorization/default-roles-<realm>, which are not task scopes)."""
    roles = claims.get("realm_access", {}).get("roles", [])
    for role in roles:
        if role in TASK_SCOPE_ROLES:
            return role
    return None
