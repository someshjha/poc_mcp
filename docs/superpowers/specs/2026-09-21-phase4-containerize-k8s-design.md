# Phase 4 — Containerize + plain Kubernetes (kind) — design

## Context

Phases 1-3 run as host processes talking to two Dockerized dependencies (Postgres, Keycloak) via `docker-compose.yml`, with `mcp_server` and `ui` run directly via `uvicorn` on the host. Phase 4 packages `mcp_server` and `ui` as container images and deploys the whole system — Postgres, Liquibase migration, Keycloak, MCP server, UI — onto a local `kind` cluster with plain `kubectl apply` manifests (no Argo CD yet — that's Phase 5). The goal is a `k8s/bootstrap.sh` that takes a bare Docker install to a fully running, verifiable system in one command, provable by re-running `scripts/verify_scopes.py`, `scripts/verify_mcp_server.py`, `scripts/verify_ui.py`, and the Karate suite against the cluster instead of docker-compose.

## Key existing facts (verified by reading the code, not assumed)

- `mcp_server/db.py`'s `DSN`, `mcp_server/server.py`'s `KEYCLOAK_ISSUER_URL`/`MCP_RESOURCE_SERVER_URL`, and `ui/app.py`'s `KEYCLOAK_URL`/`UI_REDIRECT_URI`/`MCP_SERVER_URL`/`UI_SESSION_SECRET` are already all environment-variable-overridable with `localhost`-based defaults — no code changes needed to point them at cluster-internal hostnames, **except** the two issues below.
- `mcp_server/token_verifier.py`'s `KeycloakTokenVerifier` derives the JWKS-fetch URL directly from `issuer_url` (`{issuer_url}/protocol/openid-connect/certs`) and also requires the JWT's `iss` claim to equal `issuer_url` exactly (`jwt.decode(..., issuer=self.issuer_url)`). In-cluster, these two needs conflict: the `iss` claim in tokens issued via the browser's login flow will be `http://localhost:8080/realms/financial-mcp` (because the browser reaches Keycloak through kind's host port mapping, same as today), but the `mcp-server` pod cannot resolve host-machine `localhost:8080` — it must reach Keycloak via cluster DNS (`http://keycloak.financial-mcp.svc.cluster.local:8080`) to fetch the JWKS.
- `ui/app.py`'s single `KEYCLOAK_URL` has the same split-horizon conflict: it's used both to build the browser-facing `/protocol/openid-connect/auth` redirect (must be the externally reachable `http://localhost:8080`) and for the UI pod's own server-side `/protocol/openid-connect/token` exchange call (must be the cluster-internal DNS name, since the UI pod can't reach the host's `localhost:8080` either).
- `kind` and `kubectl` are already installed locally (confirmed via `which`).
- The existing `db/liquibase.Dockerfile` (pinned `liquibase/liquibase:5.0.4`, JDBC driver `chmod 644` fix already applied) is reused as-is for the migration Job — no changes needed there.

## Goal

`kind create cluster` (or a pre-existing one) → `k8s/bootstrap.sh` → every one of Phase 1-3's verification scripts and the Karate suite pass against the cluster, unmodified in their assertions (only their target host/port changes, via kind's port mappings keeping `localhost:5432/8080/8000/5000` stable so **no script changes are required** — see Architecture).

## Non-goals

- No Argo CD / GitOps (Phase 5).
- No Ingress controller, no TLS, no production-grade secrets management (still dev-only plaintext Secrets, matching this project's established "local demo" posture from Phases 1-3's own Non-goals sections).
- No Horizontal Pod Autoscaling, no multi-replica anything — one replica per Deployment, this is a demo of the access-control architecture, not of k8s scaling patterns.
- No persistent volume backup/restore story — `kind delete cluster` is the reset button, same role `docker compose down -v` plays today.

## Architecture

### Images

- `mcp_server/Dockerfile` — `python:3.12-slim` base, installs `scripts/requirements.txt` (already has every dependency `mcp_server` needs: `psycopg2-binary`, `mcp[cli]`, `uvicorn`, `python-jose`), copies `mcp_server/`, `CMD ["uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8000"]`.
- `ui/Dockerfile` — `python:3.12-slim` base, installs `ui/requirements.txt`, copies `ui/`, `CMD ["uvicorn", "ui.app:app", "--host", "0.0.0.0", "--port", "5000"]`.
- Both built locally and loaded into the kind cluster with `kind load docker-image` (no registry needed for a local demo cluster — this is the standard kind-native workflow, not a workaround).

### Code changes (small, surgical)

1. `mcp_server/token_verifier.py`: add an optional `jwks_base_url: str | None = None` constructor param, defaulting to `issuer_url` (preserves current docker-compose/host behavior exactly). When set, `_get_jwks()` fetches from `{jwks_base_url}/protocol/openid-connect/certs` instead of `{issuer_url}/...`, while `verify_token` still validates `iss` against `issuer_url` unchanged. `mcp_server/server.py` reads a new optional `KEYCLOAK_JWKS_URL` env var and passes it through.
2. `ui/app.py`: split `KEYCLOAK_URL` into `KEYCLOAK_PUBLIC_URL` (existing default `http://localhost:8080`, used only for the browser redirect at line ~56) and `KEYCLOAK_URL` keeps its existing role as the server-side base URL for the token exchange call, defaulting to `http://localhost:8080` unchanged (so docker-compose/host behavior is identical to today with zero env vars set). In k8s, the manifest sets `KEYCLOAK_URL=http://keycloak.financial-mcp.svc.cluster.local:8080` (internal, for the pod's own token exchange) and `KEYCLOAK_PUBLIC_URL=http://localhost:8080` (external, for the browser).

### kind cluster

`k8s/kind-config.yaml` — one control-plane node, `extraPortMappings`: host `5432`→node `30432` (Postgres NodePort), `8080`→`30080` (Keycloak), `8000`→`30800` (MCP server, needed so `verify_mcp_server.py` and Karate can hit it directly without going through the UI), `5000`→`30500` (UI). Keeping these host-side ports identical to the docker-compose setup means **every existing verification script and Karate feature file works against the cluster with zero changes** — they already default to `localhost:5432/8080/8000/5000`.

### Manifests (`k8s/`, applied in order by `bootstrap.sh`)

- `00-namespace.yaml` — namespace `financial-mcp`.
- `01-secrets.yaml` — `Secret` for Postgres password (`postgres_dev_only`, matching Phase 1's seed) and Keycloak admin password (`admin_dev_only`, matching Phase 2's realm-export). Same dev-only plaintext values already committed in `docker-compose.yml` today — no new secret material, just relocated.
- `02-postgres.yaml` — `Deployment` (`postgres:16`, same image/env as `docker-compose.yml`), `Service` (`ClusterIP`, also a second `Service` type `NodePort` with `nodePort: 30432` for host access), `PersistentVolumeClaim` (small, e.g. 1Gi — kind's default local-path provisioner), readiness probe via `pg_isready` matching the existing docker-compose healthcheck.
- `03-realm-configmap.yaml` — `ConfigMap` holding `keycloak/realm-export.json` verbatim (generated by `bootstrap.sh` via `kubectl create configmap --from-file`, not hand-duplicated — single source of truth stays the committed JSON file).
- `04-keycloak.yaml` — `Deployment` (`quay.io/keycloak/keycloak:26.0`, `start-dev --import-realm`, realm JSON mounted from the ConfigMap at `/opt/keycloak/data/import/realm.json`), `Service` (ClusterIP + NodePort `30080`).
- `05-liquibase-job.yaml` — `Job` running the existing `db/liquibase.Dockerfile` image against the in-cluster Postgres Service DNS name, changelog mounted via a ConfigMap generated the same way as the realm export (`kubectl create configmap --from-file=db/changelog`). `Job` has no restart-on-success; `bootstrap.sh` waits for `kubectl wait --for=condition=complete`.
- `06-mcp-server.yaml` — `Deployment` (image `poc-mcp/mcp-server:local`, loaded via `kind load docker-image`), env: `MCP_DB_DSN` pointing at the Postgres Service DNS, `KEYCLOAK_ISSUER_URL=http://localhost:8080/realms/financial-mcp` (unchanged — must match the `iss` claim browsers/scripts get from Keycloak via the host port mapping), `KEYCLOAK_JWKS_URL=http://keycloak.financial-mcp.svc.cluster.local:8080/realms/financial-mcp` (new, internal), `MCP_RESOURCE_SERVER_URL=http://localhost:8000`. `Service` (ClusterIP + NodePort `30800`).
- `07-ui.yaml` — `Deployment` (image `poc-mcp/ui:local`), env: `KEYCLOAK_URL=http://keycloak.financial-mcp.svc.cluster.local:8080` (internal, for token exchange), `KEYCLOAK_PUBLIC_URL=http://localhost:8080` (external, for browser redirect), `UI_REDIRECT_URI=http://localhost:5000/auth/callback`, `MCP_SERVER_URL=http://mcp-server.financial-mcp.svc.cluster.local:8000/mcp`, `UI_SESSION_SECRET` from a Secret. `Service` (ClusterIP + NodePort `30500`).

### `k8s/bootstrap.sh`

One script, idempotent-ish (safe to re-run): checks/creates the kind cluster from `kind-config.yaml` if it doesn't already exist named `financial-mcp`, builds both Docker images, `kind load docker-image`s them into the cluster, generates the two `ConfigMap`s from source files (`realm-export.json`, `db/changelog/`), applies all manifests in numeric order, waits for Postgres readiness, waits for the Liquibase Job to complete, waits for Keycloak's `/realms/financial-mcp` to return 200 (same polling pattern `README.md`'s existing quickstarts already use), waits for `mcp-server`/`ui` Deployments to become `Available`, then prints next steps (run the verification scripts).

## Verification

Run, unmodified, against the cluster (relying on the port-mapping parity described above):
```bash
k8s/bootstrap.sh
pip install -r scripts/requirements.txt -r ui/requirements.txt
python3 scripts/verify_scopes.py
python3 scripts/verify_mcp_server.py
python3 scripts/verify_ui.py
karate/download-karate.sh
java -jar karate/karate.jar karate/scoped_access.feature
```
All five must pass with the exact same result counts as their docker-compose runs (verify_scopes: per-user scope proof; verify_mcp_server: 13/13; verify_ui: 10/10; Karate: 13/13) — proving the cluster deployment is behaviorally identical to the host-process deployment, not just "it started."

Also manually confirm the split-horizon JWT fix actually works end-to-end: log into the UI in a browser at `http://localhost:5000`, complete a real login through Keycloak (external URL), and confirm a Console tool call succeeds (proves the token's `iss` claim, set via the external URL, still validates correctly when the mcp-server pod fetches JWKS via the internal URL).

## Open questions / explicitly deferred

- `kind delete cluster --name financial-mcp` is the teardown story — no separate teardown script needed, this is a one-liner already in `README.md`'s new Phase 4 section.
- Image versioning/tagging beyond `:local` — irrelevant until Phase 5 needs Argo CD to track image changes; deferred to that phase's own design.
