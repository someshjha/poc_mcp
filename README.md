# poc_mcp — Scoped financial-data MCP (real implementation)

Real implementation of the "scoped financial-data tools for agents" proof of concept: an MCP server backed by Postgres, where task-scope access is enforced by Postgres roles and per-user data access is enforced by Row-Level Security -- not by application code. See the [design spec](https://github.com/someshjha/someshjha.github.io/blob/main/docs/superpowers/specs/2026-09-21-scoped-financial-mcp-real-implementation-design.md) for the full architecture and business rationale, and the [browser-only mock](https://someshjha.com/financial-mcp/) for the same demo without a real backend.

## Status

Phase 1 (Postgres schema, roles, RLS), Phase 2 (MCP server + Keycloak auth), Phase 3 (showcase UI), Phase 4 (containerize + kind), and Phase 5 (Argo CD GitOps) are complete and locally verified.

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
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/realms/financial-mcp)" = "200" ]; do sleep 1; done
pip install -r scripts/requirements.txt
uvicorn mcp_server.server:app --port 8000 &
sleep 2
python3 scripts/verify_mcp_server.py
# stop the server when done: pkill -f "uvicorn mcp_server.server"
```

`docker compose up -d keycloak` only waits for the container to be created, not for Keycloak to finish importing the realm and serving `/realms/financial-mcp` (roughly 10 seconds) -- the `until` loop above polls for that readiness before anything tries to authenticate against it.

`verify_mcp_server.py` proves, through a real MCP client and real Keycloak-issued tokens, that the running server enforces the same task-scope and account-ownership boundaries `verify_scopes.py` already proved directly against Postgres -- this time end-to-end through the actual authentication and tool-call path.

## Karate suite (independent third proof)

A [Karate](https://github.com/karatelabs/karate) suite in `karate/` proves the exact same 13 scenarios `verify_mcp_server.py` does, driven by a completely independent HTTP client (Java, not Python) speaking the raw MCP JSON-RPC/SSE protocol directly -- useful as a standard, tool-agnostic regression suite that doesn't depend on this project's own Python code being correct.

```bash
# with the Phase 2 stack already up (postgres, keycloak, migrated, uvicorn running -- see above)
karate/download-karate.sh
java -jar karate/karate.jar karate/scoped_access.feature
```

Expect `scenarios: 13 | passed: 13 | failed: 0`. An HTML report is written to `target/karate-reports/`.

## Phase 3 quickstart

Requires everything Phase 2 needs, plus the UI's own dependencies.

```bash
docker compose up -d postgres keycloak
docker compose run --rm liquibase
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/realms/financial-mcp)" = "200" ]; do sleep 1; done
pip install -r scripts/requirements.txt -r ui/requirements.txt
uvicorn mcp_server.server:app --port 8000 --log-level warning &
sleep 2
uvicorn ui.app:app --port 5000 --log-level warning &
sleep 2
python3 scripts/verify_ui.py
```

Then open `http://localhost:5000/` in a browser and log in as any of the five demo users (`alice.research`/`alice_dev_only`, `bob.risk`/`bob_dev_only`, `carol.trader`/`carol_dev_only`, `dave.support`/`dave_dev_only`, `erin.norole`/`erin_dev_only`) to see the scoped-access boundary work live, through a real login.

Stop both background servers when done: `pkill -f "uvicorn mcp_server.server"`, `pkill -f "uvicorn ui.app"`.

## Phase 4 quickstart

Requires Docker, [kind](https://kind.sigs.k8s.io/), and `kubectl`, in addition to everything Phase 3 needs.

```bash
k8s/bootstrap.sh
pip install -r scripts/requirements.txt -r ui/requirements.txt
python3 scripts/verify_scopes.py
python3 scripts/verify_mcp_server.py
python3 scripts/verify_ui.py
karate/download-karate.sh
java -jar karate/karate.jar karate/scoped_access.feature
```

`k8s/bootstrap.sh` creates a local `kind` cluster (or reuses one already named `financial-mcp`), builds and loads the `mcp-server`/`ui`/`liquibase` images, and applies every manifest in `k8s/` -- Postgres, a Liquibase migration Job, Keycloak, the MCP server, and the UI, all in the `financial-mcp` namespace. Every host port matches what Phases 1-3 already used (`5432`/`8080`/`8000`/`5000`), so every verification script and the Karate suite above run completely unmodified against the cluster.

Open `http://localhost:5000/` in a browser to use the UI exactly as in Phase 3, now backed by the Kubernetes deployment instead of host processes.

Teardown: `kind delete cluster --name financial-mcp`.

## Phase 5 quickstart (Argo CD GitOps)

Requires everything Phase 4 needs, plus a pushed branch (Argo CD syncs from git, not your working tree). If a Phase 4 cluster (deployed via plain `kubectl apply`, not Argo CD) is still running, tear it down first -- Argo CD will collide with that deployment's untracked `ConfigMap`s and Job rather than adopting them cleanly: `kind delete cluster --name financial-mcp`.

```bash
git push -u origin claude/db-schema-rls
argocd/bootstrap.sh
kubectl get application financial-mcp -n argocd
pip install -r scripts/requirements.txt -r ui/requirements.txt
python3 scripts/verify_scopes.py
python3 scripts/verify_mcp_server.py
python3 scripts/verify_ui.py
java -jar karate/karate.jar karate/scoped_access.feature
```

`argocd/bootstrap.sh` creates (or reuses) the `kind` cluster, builds and loads the same three local images Phase 4 used, installs Argo CD, and applies a single `Application` that syncs everything under the repo root's `kustomization.yaml` (which pulls in `k8s/`, `keycloak/`, and `db/`) from this repo's `claude/db-schema-rls` branch -- change `targetRevision` in `argocd/application.yaml` to `main` once this work is merged. From here on, `kubectl apply` is no longer how you deploy: edit a manifest, commit, push, and Argo CD reconciles the cluster automatically (`syncPolicy.automated` with `selfHeal: true` -- it also reverts any manual `kubectl edit` drift back to what's in git).

Argo CD UI: `kubectl port-forward svc/argocd-server -n argocd 8081:443`, then open `https://localhost:8081` (username `admin`, password printed by `argocd/install.sh`).

Teardown: `kind delete cluster --name financial-mcp` (also removes Argo CD, which lives in the same cluster).
