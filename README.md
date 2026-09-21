# poc_mcp — Scoped financial-data MCP (real implementation)

Real implementation of the "scoped financial-data tools for agents" proof of concept: an MCP server backed by Postgres, where task-scope access is enforced by Postgres roles and per-user data access is enforced by Row-Level Security -- not by application code. See the [design spec](https://github.com/someshjha/someshjha.github.io/blob/main/docs/superpowers/specs/2026-09-21-scoped-financial-mcp-real-implementation-design.md) for the full architecture and business rationale, and the [browser-only mock](https://someshjha.com/financial-mcp/) for the same demo without a real backend.

## Status

Phase 1 (Postgres schema, roles, RLS) and Phase 2 (MCP server + Keycloak auth) are complete and locally verified. The showcase UI and Kubernetes/Argo CD deployment are later phases, not yet built.

## Phase 1 quickstart

Requires Docker and Python 3.

```bash
docker compose up -d postgres
docker compose run --rm liquibase
pip install -r scripts/requirements.txt
python3 scripts/verify_scopes.py
```

The verification script proves, independently of any server, that:
- Each of the four demo users (`alice.research`, `bob.risk`, `carol.trader`, `dave.support`) can only reach the Postgres tables their task-scope role (`equity_research`, `portfolio_risk`, `trade_execution`, `client_support`) is granted.
- Within those tables, each user sees only rows for accounts they own (via `account_owners`), even though `bob.risk` and `carol.trader` share ownership of `ACC-1001` -- proving the role boundary and the ownership boundary are checked independently.
- Every attempt, allowed or denied, is recorded in `audit_log`.

To reset the database and start clean:
```bash
docker compose down -v
```

## Phase 2 quickstart

Requires everything Phase 1 needs, plus Keycloak (already added to `docker-compose.yml`).

```bash
docker compose up -d postgres keycloak
docker compose run --rm liquibase
pip install -r scripts/requirements.txt
uvicorn mcp_server.server:app --port 8000 &
python3 scripts/verify_mcp_server.py
```

`verify_mcp_server.py` proves, through a real MCP client and real Keycloak-issued tokens, that the running server enforces the same task-scope and account-ownership boundaries `verify_scopes.py` already proved directly against Postgres -- this time end-to-end through the actual authentication and tool-call path.
