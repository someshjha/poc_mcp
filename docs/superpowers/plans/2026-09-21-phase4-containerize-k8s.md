# Phase 4 — Containerize + plain Kubernetes (kind) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package `mcp_server` and `ui` as container images and deploy the full system (Postgres, Liquibase migration, Keycloak, MCP server, UI) onto a local `kind` cluster via plain `kubectl apply` manifests, with every existing Phase 1-3 verification script and the Karate suite passing against the cluster unmodified.

**Architecture:** See `docs/superpowers/specs/2026-09-21-phase4-containerize-k8s-design.md` for full rationale. Summary: two new Dockerfiles, two small code changes to decouple Keycloak's externally-visible URL (browser-facing, `iss` claim) from its internally-reachable URL (JWKS fetch, server-side token exchange), a `kind` cluster whose host port mappings exactly match today's docker-compose ports so no script changes are needed, k8s manifests applied in numeric order, and a `k8s/bootstrap.sh` orchestrating all of it.

**Tech Stack:** Docker, kind, kubectl, Python 3.12-slim images, existing FastAPI/MCP/Postgres/Keycloak stack unchanged.

## Global Constraints

- Host-side ports MUST stay `5432` (Postgres), `8080` (Keycloak), `8000` (MCP server), `5000` (UI) — identical to `docker-compose.yml` — so `scripts/verify_scopes.py`, `scripts/verify_mcp_server.py`, `scripts/verify_ui.py`, and `karate/*.feature` require zero changes.
- `KEYCLOAK_ISSUER_URL` for `mcp_server` stays `http://localhost:8080/realms/financial-mcp` (must match the `iss` claim issued via the externally-reachable Keycloak URL) — only the JWKS-fetch path becomes internal, via a new `KEYCLOAK_JWKS_URL` env var.
- `ui`'s browser-facing Keycloak URL (`KEYCLOAK_PUBLIC_URL`, new) stays `http://localhost:8080`; its server-side token-exchange URL (`KEYCLOAK_URL`, existing var, default unchanged) becomes `http://keycloak.financial-mcp.svc.cluster.local:8080` only in the k8s manifest — the Python default stays `http://localhost:8080` so docker-compose/host behavior is byte-identical to today with zero env vars set.
- Namespace: `financial-mcp` for every k8s object.
- Dev-only plaintext secret values must exactly match what's already committed in `docker-compose.yml`/`keycloak/realm-export.json` today (`postgres_dev_only`, `admin_dev_only`) — no new secret material invented.
- Reuse `db/liquibase.Dockerfile` as-is (already pinned, already has the JDBC-driver permission fix) — do not modify it.
- One replica per Deployment. No Ingress, no TLS, no autoscaling (Non-goals in the spec).

---

### Task 1: Dockerize `mcp_server` and `ui`, decouple Keycloak's internal/external URLs

**Files:**
- Create: `mcp_server/Dockerfile`
- Create: `ui/Dockerfile`
- Create: `.dockerignore` (repo root)
- Modify: `mcp_server/token_verifier.py`
- Modify: `mcp_server/server.py`
- Modify: `ui/app.py`
- Test: `tests/test_token_verifier.py` (extend existing file)

**Interfaces:**
- Consumes: `mcp_server/tools.py`'s existing 8 tool functions (unchanged), `scripts/requirements.txt` and `ui/requirements.txt` (unchanged, already complete).
- Produces: two buildable Docker images (`poc-mcp/mcp-server:local`, `poc-mcp/ui:local`); `KeycloakTokenVerifier(issuer_url, jwks_base_url=None, ...)` constructor signature that Task 2's manifests rely on via the `KEYCLOAK_JWKS_URL` env var; `ui/app.py`'s new `KEYCLOAK_PUBLIC_URL` env var that Task 2's UI manifest sets.

- [ ] **Step 1: Write the failing test for `KeycloakTokenVerifier`'s JWKS URL decoupling**

Add to `tests/test_token_verifier.py` (read the existing file first to match its exact mocking/fixture style before writing this — it already mocks `urllib.request.urlopen` for the JWKS fetch and constructs JWTs signed with a test RSA key):

```python
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

def test_get_jwks_fetches_from_jwks_base_url_not_issuer_url(self, mock_urlopen):
    # follow this file's existing pattern for mocking urllib.request.urlopen
    # (see the existing JWKS-fetch test above/below this one for the exact
    # mock setup this codebase already uses); assert the URL passed to
    # urlopen starts with "http://keycloak.financial-mcp.svc.cluster.local:8080"
    # when jwks_base_url is set to that value, NOT the issuer_url.
    ...
```

Adapt the third test to this file's actual existing mock style (read the file first — do not guess the mock API).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m unittest tests.test_token_verifier -v`
Expected: FAIL — `KeycloakTokenVerifier` has no `jwks_base_url` attribute yet.

- [ ] **Step 3: Implement the decoupling in `mcp_server/token_verifier.py`**

```python
class KeycloakTokenVerifier(TokenVerifier):
    def __init__(
        self,
        issuer_url: str,
        audience: str = "account",
        jwks_ttl_seconds: int = 300,
        jwks_base_url: str | None = None,
    ):
        self.issuer_url = issuer_url.rstrip("/")
        self.jwks_base_url = (jwks_base_url or issuer_url).rstrip("/")
        self.audience = audience
        self.jwks_ttl_seconds = jwks_ttl_seconds
        self._jwks = None
        self._jwks_fetched_at = 0.0

    def _get_jwks(self) -> dict:
        now = time.monotonic()
        if self._jwks is None or (now - self._jwks_fetched_at) > self.jwks_ttl_seconds:
            url = f"{self.jwks_base_url}/protocol/openid-connect/certs"
            with urllib.request.urlopen(url, timeout=5) as resp:
                self._jwks = json.load(resp)
            self._jwks_fetched_at = now
        return self._jwks
```
`verify_token`'s `jwt.decode(..., issuer=self.issuer_url)` call is unchanged — only `_get_jwks`'s URL source changes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_token_verifier -v`
Expected: PASS, and the full existing suite in this file still passes (no regressions).

- [ ] **Step 5: Wire `KEYCLOAK_JWKS_URL` through `mcp_server/server.py`**

```python
ISSUER_URL = os.environ.get("KEYCLOAK_ISSUER_URL", "http://localhost:8080/realms/financial-mcp")
JWKS_BASE_URL = os.environ.get("KEYCLOAK_JWKS_URL")  # None -> defaults to ISSUER_URL
RESOURCE_SERVER_URL = os.environ.get("MCP_RESOURCE_SERVER_URL", "http://localhost:8000")

server = MCPServer(
    "financial-mcp",
    token_verifier=KeycloakTokenVerifier(ISSUER_URL, jwks_base_url=JWKS_BASE_URL),
    auth=AuthSettings(issuer_url=ISSUER_URL, resource_server_url=RESOURCE_SERVER_URL),
)
```

- [ ] **Step 6: Split `ui/app.py`'s Keycloak URL into public (browser) vs internal (server-side)**

Read `ui/app.py` in full first — it's already been through 3 review rounds this session, keep every other line unchanged. Change only:

```python
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_PUBLIC_URL = os.environ.get("KEYCLOAK_PUBLIC_URL", KEYCLOAK_URL)
```

Then in the `login()` route handler, change the line building `auth_url` from `f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/auth"` to `f"{KEYCLOAK_PUBLIC_URL}/realms/{REALM}/protocol/openid-connect/auth"`. Leave the token-exchange call (`auth_callback`, the `POST .../token` request) using `KEYCLOAK_URL` unchanged — that's the server-side call and stays on the internal/default URL.

- [ ] **Step 7: Write `mcp_server/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY scripts/requirements.txt ./scripts/requirements.txt
RUN pip install --no-cache-dir -r scripts/requirements.txt
COPY mcp_server ./mcp_server
EXPOSE 8000
CMD ["uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 8: Write `ui/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY ui/requirements.txt ./ui/requirements.txt
RUN pip install --no-cache-dir -r ui/requirements.txt
COPY ui ./ui
EXPOSE 5000
CMD ["uvicorn", "ui.app:app", "--host", "0.0.0.0", "--port", "5000"]
```

Note both Dockerfiles use build context = repo root (so `mcp_server`'s Dockerfile can `COPY ui` is NOT needed — each only copies its own package plus its own requirements file; build them with `docker build -f mcp_server/Dockerfile .` and `docker build -f ui/Dockerfile .` respectively, both from repo root, so relative `COPY` paths resolve).

- [ ] **Step 9: Write `.dockerignore`**

```
.git
.worktrees
.superpowers
docs
karate
__pycache__
*.pyc
.venv
```

- [ ] **Step 10: Build both images locally and smoke-test them**

Run:
```bash
docker build -f mcp_server/Dockerfile -t poc-mcp/mcp-server:local .
docker build -f ui/Dockerfile -t poc-mcp/ui:local .
docker run --rm poc-mcp/mcp-server:local python3 -c "import mcp_server.server"
docker run --rm poc-mcp/ui:local python3 -c "import ui.app"
```
Expected: both builds succeed, both import checks exit 0 with no errors (proves all dependencies installed correctly and the module imports cleanly, without needing a live DB/Keycloak yet).

- [ ] **Step 11: Run the full existing unit test suite to confirm no regressions**

Run: `python3 -m unittest tests.test_scope tests.test_db tests.test_tools tests.test_token_verifier -v`
Expected: all pass (same count as before this task, plus the 3 new tests from Step 1).

- [ ] **Step 12: Commit**

```bash
git add mcp_server/Dockerfile ui/Dockerfile .dockerignore mcp_server/token_verifier.py mcp_server/server.py ui/app.py tests/test_token_verifier.py
git commit -m "Dockerize mcp_server and ui; decouple Keycloak's internal/external URLs

mcp_server's KeycloakTokenVerifier now fetches its JWKS from an
independently-configurable jwks_base_url (falling back to issuer_url,
so docker-compose/host behavior is unchanged) while still validating
the iss claim against issuer_url -- needed because a k8s-deployed
mcp-server pod can't reach the host-facing localhost:8080 Keycloak URL
that browsers and scripts use to obtain tokens, only the in-cluster
Service DNS name.

ui/app.py similarly splits KEYCLOAK_PUBLIC_URL (browser-facing OAuth
redirect) from KEYCLOAK_URL (server-side token exchange, default
unchanged) for the same reason.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: k8s manifests + kind cluster config

**Files:**
- Create: `k8s/kind-config.yaml`
- Create: `k8s/00-namespace.yaml`
- Create: `k8s/01-secrets.yaml`
- Create: `k8s/02-postgres.yaml`
- Create: `k8s/04-keycloak.yaml`
- Create: `k8s/05-liquibase-job.yaml`
- Create: `k8s/06-mcp-server.yaml`
- Create: `k8s/07-ui.yaml`

(No `03-realm-configmap.yaml`/changelog-configmap file — those `ConfigMap`s are generated at apply-time by `bootstrap.sh` from the existing source files, per the spec, to keep one source of truth. Task 3 writes `bootstrap.sh`.)

**Interfaces:**
- Consumes: Task 1's two image tags (`poc-mcp/mcp-server:local`, `poc-mcp/ui:local`), the `KEYCLOAK_JWKS_URL`/`KEYCLOAK_PUBLIC_URL` env vars Task 1 added, the existing `db/liquibase.Dockerfile` image (built the same way `docker-compose.yml`'s `liquibase` service already builds it — `docker build -f db/liquibase.Dockerfile -t poc-mcp/liquibase:local db/`).
- Produces: a `financial-mcp` namespace with Postgres/Keycloak/mcp-server/ui all reachable both in-cluster (via `<name>.financial-mcp.svc.cluster.local`) and from the host (via the NodePort/extraPortMapping combination on `localhost:5432/8080/8000/5000`), that Task 3's `bootstrap.sh` applies and Task 4 verifies.

- [ ] **Step 1: Write `k8s/kind-config.yaml`**

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: financial-mcp
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30432
        hostPort: 5432
        protocol: TCP
      - containerPort: 30080
        hostPort: 8080
        protocol: TCP
      - containerPort: 30800
        hostPort: 8000
        protocol: TCP
      - containerPort: 30500
        hostPort: 5000
        protocol: TCP
```

- [ ] **Step 2: Write `k8s/00-namespace.yaml`**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: financial-mcp
```

- [ ] **Step 3: Write `k8s/01-secrets.yaml`**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: postgres-credentials
  namespace: financial-mcp
type: Opaque
stringData:
  POSTGRES_USER: postgres
  POSTGRES_PASSWORD: postgres_dev_only
  POSTGRES_DB: financial_mcp
---
apiVersion: v1
kind: Secret
metadata:
  name: keycloak-credentials
  namespace: financial-mcp
type: Opaque
stringData:
  KC_BOOTSTRAP_ADMIN_USERNAME: admin
  KC_BOOTSTRAP_ADMIN_PASSWORD: admin_dev_only
---
apiVersion: v1
kind: Secret
metadata:
  name: ui-session-secret
  namespace: financial-mcp
type: Opaque
stringData:
  UI_SESSION_SECRET: dev-only-session-secret
```

(Values copied verbatim from `docker-compose.yml`/`ui/app.py`'s own dev-only defaults — see Global Constraints.)

- [ ] **Step 4: Write `k8s/02-postgres.yaml`**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
  namespace: financial-mcp
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: financial-mcp
spec:
  replicas: 1
  selector:
    matchLabels: { app: postgres }
  template:
    metadata:
      labels: { app: postgres }
    spec:
      containers:
        - name: postgres
          image: postgres:16
          envFrom:
            - secretRef: { name: postgres-credentials }
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "postgres", "-d", "financial_mcp"]
            initialDelaySeconds: 2
            periodSeconds: 2
          livenessProbe:
            exec:
              command: ["pg_isready", "-U", "postgres", "-d", "financial_mcp"]
            initialDelaySeconds: 10
            periodSeconds: 10
      volumes:
        - name: data
          persistentVolumeClaim: { claimName: postgres-data }
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: financial-mcp
spec:
  selector: { app: postgres }
  ports:
    - port: 5432
      targetPort: 5432
---
apiVersion: v1
kind: Service
metadata:
  name: postgres-external
  namespace: financial-mcp
spec:
  type: NodePort
  selector: { app: postgres }
  ports:
    - port: 5432
      targetPort: 5432
      nodePort: 30432
```

- [ ] **Step 5: Write `k8s/04-keycloak.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak
  namespace: financial-mcp
spec:
  replicas: 1
  selector:
    matchLabels: { app: keycloak }
  template:
    metadata:
      labels: { app: keycloak }
    spec:
      containers:
        - name: keycloak
          image: quay.io/keycloak/keycloak:26.0
          args: ["start-dev", "--import-realm"]
          envFrom:
            - secretRef: { name: keycloak-credentials }
          ports:
            - containerPort: 8080
          volumeMounts:
            - name: realm-import
              mountPath: /opt/keycloak/data/import
          readinessProbe:
            httpGet: { path: /realms/financial-mcp, port: 8080 }
            initialDelaySeconds: 15
            periodSeconds: 5
            failureThreshold: 20
      volumes:
        - name: realm-import
          configMap: { name: keycloak-realm-export }
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak
  namespace: financial-mcp
spec:
  selector: { app: keycloak }
  ports:
    - port: 8080
      targetPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak-external
  namespace: financial-mcp
spec:
  type: NodePort
  selector: { app: keycloak }
  ports:
    - port: 8080
      targetPort: 8080
      nodePort: 30080
```

(`keycloak-realm-export` ConfigMap is generated by `bootstrap.sh`, Task 3 — this Deployment just references it by name.)

- [ ] **Step 6: Write `k8s/05-liquibase-job.yaml`**

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: liquibase-migrate
  namespace: financial-mcp
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: liquibase
          image: poc-mcp/liquibase:local
          workingDir: /liquibase/changelog
          command: ["liquibase"]
          args:
            - "--url=jdbc:postgresql://postgres.financial-mcp.svc.cluster.local:5432/financial_mcp"
            - "--username=postgres"
            - "--password=postgres_dev_only"
            - "--changelog-file=changelog.xml"
            - "update"
          volumeMounts:
            - name: changelog
              mountPath: /liquibase/changelog
      volumes:
        - name: changelog
          configMap: { name: liquibase-changelog }
```

Read `db/liquibase.properties` first to confirm the exact `--changelog-file` name and any other flags it sets that must be mirrored here as CLI args (the Job doesn't have `liquibase.properties` available unless also mounted — prefer mounting `db/liquibase.properties` itself via the same `liquibase-changelog` ConfigMap and using `--defaultsFile=liquibase.properties` exactly as `docker-compose.yml`'s `liquibase` service already does, instead of reconstructing the flags by hand. Adjust this manifest's `command`/`args` to `entrypoint: ["liquibase"], command: ["--defaultsFile=liquibase.properties", "update"]`-equivalent k8s syntax if that's simpler and more consistent with the existing docker-compose service — your judgment, but it must actually work, verified in Task 4).

- [ ] **Step 7: Write `k8s/06-mcp-server.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-server
  namespace: financial-mcp
spec:
  replicas: 1
  selector:
    matchLabels: { app: mcp-server }
  template:
    metadata:
      labels: { app: mcp-server }
    spec:
      containers:
        - name: mcp-server
          image: poc-mcp/mcp-server:local
          imagePullPolicy: Never
          env:
            - name: MCP_DB_DSN
              value: "host=postgres.financial-mcp.svc.cluster.local port=5432 dbname=financial_mcp user=app_pool password=app_pool_dev_only"
            - name: KEYCLOAK_ISSUER_URL
              value: "http://localhost:8080/realms/financial-mcp"
            - name: KEYCLOAK_JWKS_URL
              value: "http://keycloak.financial-mcp.svc.cluster.local:8080/realms/financial-mcp"
            - name: MCP_RESOURCE_SERVER_URL
              value: "http://localhost:8000"
          ports:
            - containerPort: 8000
          readinessProbe:
            tcpSocket: { port: 8000 }
            initialDelaySeconds: 3
            periodSeconds: 3
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-server
  namespace: financial-mcp
spec:
  selector: { app: mcp-server }
  ports:
    - port: 8000
      targetPort: 8000
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-server-external
  namespace: financial-mcp
spec:
  type: NodePort
  selector: { app: mcp-server }
  ports:
    - port: 8000
      targetPort: 8000
      nodePort: 30800
```

`app_pool_dev_only` must exactly match `db/changelog`'s seeded `app_pool` role password — verify this value by reading `db/changelog/002-roles-grants.sql` (or wherever `app_pool`'s password is set) before finalizing; do not guess.

- [ ] **Step 8: Write `k8s/07-ui.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ui
  namespace: financial-mcp
spec:
  replicas: 1
  selector:
    matchLabels: { app: ui }
  template:
    metadata:
      labels: { app: ui }
    spec:
      containers:
        - name: ui
          image: poc-mcp/ui:local
          imagePullPolicy: Never
          env:
            - name: KEYCLOAK_URL
              value: "http://keycloak.financial-mcp.svc.cluster.local:8080"
            - name: KEYCLOAK_PUBLIC_URL
              value: "http://localhost:8080"
            - name: UI_REDIRECT_URI
              value: "http://localhost:5000/auth/callback"
            - name: MCP_SERVER_URL
              value: "http://mcp-server.financial-mcp.svc.cluster.local:8000/mcp"
          envFrom:
            - secretRef: { name: ui-session-secret }
          ports:
            - containerPort: 5000
          readinessProbe:
            tcpSocket: { port: 5000 }
            initialDelaySeconds: 3
            periodSeconds: 3
---
apiVersion: v1
kind: Service
metadata:
  name: ui
  namespace: financial-mcp
spec:
  selector: { app: ui }
  ports:
    - port: 5000
      targetPort: 5000
---
apiVersion: v1
kind: Service
metadata:
  name: ui-external
  namespace: financial-mcp
spec:
  type: NodePort
  selector: { app: ui }
  ports:
    - port: 5000
      targetPort: 5000
      nodePort: 30500
```

- [ ] **Step 9: Commit**

```bash
git add k8s/kind-config.yaml k8s/00-namespace.yaml k8s/01-secrets.yaml k8s/02-postgres.yaml k8s/04-keycloak.yaml k8s/05-liquibase-job.yaml k8s/06-mcp-server.yaml k8s/07-ui.yaml
git commit -m "Add k8s manifests for Postgres, Keycloak, Liquibase migration Job, mcp-server, ui

Namespace financial-mcp, one Deployment+Service per component plus a
NodePort Service for each so kind's extraPortMappings keep the same
host ports (5432/8080/8000/5000) docker-compose already used -- every
existing verification script and the Karate suite need zero changes
to run against this cluster instead.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `k8s/bootstrap.sh` + README

**Files:**
- Create: `k8s/bootstrap.sh`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1's two Dockerfiles and Task 2's manifests/kind-config exactly as written.
- Produces: a single command that takes a bare Docker+kind+kubectl install to a fully running, ready cluster; documented in `README.md`'s Phase 4 section for Task 4's verification and for the user to run themselves.

- [ ] **Step 1: Write `k8s/bootstrap.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER_NAME="financial-mcp"
NAMESPACE="financial-mcp"

if ! kind get clusters | grep -qx "$CLUSTER_NAME"; then
  echo "Creating kind cluster '$CLUSTER_NAME'..."
  kind create cluster --config k8s/kind-config.yaml
else
  echo "kind cluster '$CLUSTER_NAME' already exists, reusing."
fi

echo "Building images..."
docker build -f mcp_server/Dockerfile -t poc-mcp/mcp-server:local .
docker build -f ui/Dockerfile -t poc-mcp/ui:local .
docker build -f db/liquibase.Dockerfile -t poc-mcp/liquibase:local db

echo "Loading images into kind..."
kind load docker-image poc-mcp/mcp-server:local --name "$CLUSTER_NAME"
kind load docker-image poc-mcp/ui:local --name "$CLUSTER_NAME"
kind load docker-image poc-mcp/liquibase:local --name "$CLUSTER_NAME"

kubectl apply -f k8s/00-namespace.yaml

echo "Generating ConfigMaps from source files..."
kubectl create configmap keycloak-realm-export \
  --from-file=realm.json=keycloak/realm-export.json \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap liquibase-changelog \
  --from-file=db/changelog \
  --from-file=db/liquibase.properties \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f k8s/01-secrets.yaml
kubectl apply -f k8s/02-postgres.yaml

echo "Waiting for Postgres..."
kubectl rollout status deployment/postgres -n "$NAMESPACE" --timeout=120s

kubectl apply -f k8s/05-liquibase-job.yaml
echo "Waiting for Liquibase migration Job..."
kubectl wait --for=condition=complete job/liquibase-migrate -n "$NAMESPACE" --timeout=120s

kubectl apply -f k8s/04-keycloak.yaml
echo "Waiting for Keycloak..."
kubectl rollout status deployment/keycloak -n "$NAMESPACE" --timeout=180s
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/realms/financial-mcp)" = "200" ]; do sleep 2; done

kubectl apply -f k8s/06-mcp-server.yaml
kubectl apply -f k8s/07-ui.yaml
echo "Waiting for mcp-server and ui..."
kubectl rollout status deployment/mcp-server -n "$NAMESPACE" --timeout=60s
kubectl rollout status deployment/ui -n "$NAMESPACE" --timeout=60s

echo ""
echo "Cluster ready. Verify with:"
echo "  pip install -r scripts/requirements.txt -r ui/requirements.txt"
echo "  python3 scripts/verify_scopes.py"
echo "  python3 scripts/verify_mcp_server.py"
echo "  python3 scripts/verify_ui.py"
echo "  java -jar karate/karate.jar karate/scoped_access.feature"
echo ""
echo "UI: http://localhost:5000/   Keycloak admin: http://localhost:8080/admin (admin/admin_dev_only)"
echo "Teardown: kind delete cluster --name $CLUSTER_NAME"
```

`chmod +x k8s/bootstrap.sh` after writing it.

The Liquibase Job's `command`/`args`/ConfigMap-mount approach in `k8s/05-liquibase-job.yaml` (Task 2 Step 6) and this script's `liquibase-changelog` ConfigMap generation must agree on exactly which files get mounted and what flags Liquibase is invoked with — reconcile any mismatch you find between Task 2's file and this step before moving on; this whole path gets exercised for real in Task 4 so don't leave it just "probably right."

- [ ] **Step 2: Add a Phase 4 section to `README.md`**

Read `README.md` in full first (it's short, already reproduced in full in a prior part of this project's history) and match its existing style exactly (each phase gets a `## Phase N quickstart` section with a fenced bash block and a short explanation paragraph after). Update the `## Status` line to add "Phase 4 (containerize + kind)" to the completed list. Add:

```markdown
## Phase 4 quickstart

Requires Docker, [kind](https://kind.sigs.k8s.io/), and `kubectl`, in addition to everything Phase 3 needs.

\`\`\`bash
k8s/bootstrap.sh
pip install -r scripts/requirements.txt -r ui/requirements.txt
python3 scripts/verify_scopes.py
python3 scripts/verify_mcp_server.py
python3 scripts/verify_ui.py
karate/download-karate.sh
java -jar karate/karate.jar karate/scoped_access.feature
\`\`\`

`k8s/bootstrap.sh` creates a local `kind` cluster (or reuses one already named `financial-mcp`), builds and loads the `mcp-server`/`ui`/`liquibase` images, and applies every manifest in `k8s/` -- Postgres, a Liquibase migration Job, Keycloak, the MCP server, and the UI, all in the `financial-mcp` namespace. Every host port matches what Phases 1-3 already used (`5432`/`8080`/`8000`/`5000`), so every verification script and the Karate suite above run completely unmodified against the cluster.

Open `http://localhost:5000/` in a browser to use the UI exactly as in Phase 3, now backed by the Kubernetes deployment instead of host processes.

Teardown: `kind delete cluster --name financial-mcp`.
```

(Use real newlines/backticks when writing the file -- the `\`\`\`` above is escaped only for this plan document.)

- [ ] **Step 3: Commit**

```bash
git add k8s/bootstrap.sh README.md
git commit -m "Add k8s/bootstrap.sh and Phase 4 README quickstart

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: End-to-end verification against the live cluster

**Files:** None created/modified — this task is pure verification, but is a first-class task with its own review because it's the only thing that actually proves Tasks 1-3 work together (each of those tasks builds artifacts that can't be fully proven in isolation: Task 1's images need a real cluster to run against, Task 2's manifests need Task 3's script to apply them in the right order).

**Interfaces:**
- Consumes: everything from Tasks 1-3 exactly as committed.
- Produces: a verification report proving the cluster is behaviorally identical to the docker-compose deployment.

- [ ] **Step 1: Tear down any stray previous cluster/containers to start clean**

```bash
kind delete cluster --name financial-mcp 2>/dev/null || true
docker compose down -v 2>/dev/null || true
```

(The docker-compose stack and the kind cluster both want ports 5432/8080, so they can't run simultaneously — this step guarantees Task 4 proves the k8s path in isolation, not a docker-compose container silently answering the verification scripts instead.)

- [ ] **Step 2: Run bootstrap and capture its full output**

```bash
k8s/bootstrap.sh 2>&1 | tee /tmp/bootstrap-output.log
```
Expected: exits 0, ends with the "Cluster ready" banner.

- [ ] **Step 3: Run every verification script and the Karate suite**

```bash
pip install -r scripts/requirements.txt -r ui/requirements.txt
python3 scripts/verify_scopes.py
python3 scripts/verify_mcp_server.py
python3 scripts/verify_ui.py
karate/download-karate.sh
java -jar karate/karate.jar karate/scoped_access.feature
```
Expected: `verify_scopes.py` proves the same role/RLS boundaries Phase 1 proved; `verify_mcp_server.py` reports 13/13 (or whatever its current count is post-Phase-3 fix wave — check the actual current script for the real number, don't assume 13 is still current); `verify_ui.py` reports 10/10; Karate reports `scenarios: 13 | passed: 13 | failed: 0`. If any count differs from the docker-compose baseline, that's a real regression to root-cause and fix, not something to wave through with an adjusted expectation.

- [ ] **Step 4: Manually verify the split-horizon JWT fix live in a browser**

Log into `http://localhost:5000/` as `alice.research`/`alice_dev_only` through the real Keycloak login form, then make a Console tool call (e.g. `get_market_snapshot`) and confirm it returns `{"decision": "allow", ...}` with real data — this proves a token whose `iss` claim was set via the external `localhost:8080` URL is successfully verified by the mcp-server pod fetching JWKS via the internal cluster-DNS URL, which is the core technical risk this whole phase introduced.

- [ ] **Step 5: Check `kubectl get pods -n financial-mcp`, confirm no CrashLoopBackOff / restarts**

```bash
kubectl get pods -n financial-mcp
```
Expected: every pod `Running`, `1/1` or `READY` as appropriate, `RESTARTS` column all `0` (a restarting pod means a probe or startup issue masked by k8s's own retry behavior — investigate and fix if found, don't just note it "eventually became ready").

- [ ] **Step 6: Leave the cluster running (do not tear down) — later phases and the final human review may want to inspect it**

No step needed here beyond not running `kind delete cluster`.

- [ ] **Step 7: Write a verification report and commit it**

Write `docs/superpowers/specs/2026-09-21-phase4-verification-report.md` (or wherever this plan's SDD workspace convention places task reports — follow whatever `scripts/sdd-workspace`/`task-brief` tooling the subagent-driven-development skill already set up for this plan) summarizing exact pass/fail counts from Step 3, the pod status from Step 5, and confirmation of Step 4. This is evidence for the final whole-branch review, not just this task's own review.

```bash
git add -A
git commit -m "Verify Phase 4 k8s deployment end-to-end: all scripts + Karate pass against the cluster

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

(Only commit if Step 7's report file is meant to be a tracked doc rather than an SDD-workspace scratch artifact — match whatever convention Phase 3's Task 3 report used, which was NOT committed to the repo proper, only the ledger referenced it. Default to NOT committing a new top-level doc for this if the SDD workspace already captures it via the ledger; use your judgment based on what you find when you check.)
