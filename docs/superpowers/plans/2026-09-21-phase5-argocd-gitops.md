# Phase 5 — Argo CD GitOps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 4's imperative `kubectl apply`/`ConfigMap`-generation deploy with Argo CD: a single, Kustomize-sourced `Application` that syncs `k8s/` from this repo's git remote, with automated sync/self-heal/prune, proven by making a real tracked-file change, pushing it, and watching Argo CD reconcile the cluster without any manual `kubectl apply`.

**Architecture:** See `docs/superpowers/specs/2026-09-21-phase5-argocd-gitops-design.md` for full rationale, especially the "one Application, not app-of-apps" decision. Summary: `k8s/kustomization.yaml` adds declarative `configMapGenerator`s replacing Phase 4's shell-generated `ConfigMap`s; `argocd/install.sh` installs Argo CD; `argocd/application.yaml` is the single `Application` resource; `argocd/bootstrap.sh` is the new one-command entry point.

**Tech Stack:** Argo CD (stable, official install manifest), Kustomize (via `kubectl kustomize` / `kustomize build`, no new binary required — `kubectl` has it built in), existing k8s manifests unchanged in content, just reorganized under Kustomize.

## Global Constraints

- Exactly one Argo CD `Application` — no app-of-apps split (spec's explicit decision).
- `k8s/kustomization.yaml`'s two `configMapGenerator`s must produce `ConfigMap` contents byte-identical to what Phase 4's `kubectl create configmap --from-file=...` commands already produced (same keys: `realm.json` for the Keycloak one; `liquibase.properties`, `changelog-master.xml`, `001-schema.sql`, `002-roles-grants.sql`, `003-rls.sql`, `004-seed.sql` for the Liquibase one) — only the generation mechanism changes, not the content or the keys `k8s/04-keycloak.yaml`/`k8s/05-liquibase-job.yaml` already reference.
- Keep Kustomize's default name-suffix-hashing behavior (do not set `disableNameSuffixHash: true`) — this is a deliberate improvement over Phase 4's static ConfigMap names, called out in the spec's Verification section.
- Do not modify the *content* of any existing `k8s/*.yaml` manifest from Phase 4 — Task 1 only adds a new `kustomization.yaml` alongside them (the existing files' `configMapRef`/`configMap.name` fields already reference `keycloak-realm-export`/`liquibase-changelog` by their base names, which is exactly what Kustomize's generator + nameReference transformer expects — no edits needed there).
- Namespace `financial-mcp` unchanged. Argo CD itself installs into its own new `argocd` namespace.
- No new external dependencies beyond what `kubectl` (which bundles Kustomize) and the official Argo CD install manifest already provide — do not require a separately-installed `kustomize` or `argocd` CLI binary as a hard requirement (the `argocd` CLI is a nice-to-have `argocd/bootstrap.sh` degrades gracefully without).

---

### Task 1: Kustomize-ify `k8s/` — declarative ConfigMap generation

**Files:**
- Create: `k8s/kustomization.yaml`

**Interfaces:**
- Consumes: the 8 existing `k8s/*.yaml` files (unchanged), `keycloak/realm-export.json`, `db/liquibase.properties`, `db/changelog/*.sql`, `db/changelog/changelog-master.xml` (all unchanged, already the source of truth for Phase 4's `ConfigMap` generation).
- Produces: `kubectl kustomize k8s` (or `kubectl apply -k k8s`) emits every resource Phase 4's `k8s/bootstrap.sh` applied, including both `ConfigMap`s, with identical `data` keys/values — verified byte-for-byte against Phase 4's live cluster in this task's own verification step.

- [ ] **Step 1: Write `k8s/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: financial-mcp
resources:
  - 00-namespace.yaml
  - 01-secrets.yaml
  - 02-postgres.yaml
  - 04-keycloak.yaml
  - 05-liquibase-job.yaml
  - 06-mcp-server.yaml
  - 07-ui.yaml
configMapGenerator:
  - name: keycloak-realm-export
    files:
      - realm.json=../keycloak/realm-export.json
  - name: liquibase-changelog
    files:
      - ../db/liquibase.properties
      - ../db/changelog/changelog-master.xml
      - ../db/changelog/001-schema.sql
      - ../db/changelog/002-roles-grants.sql
      - ../db/changelog/003-rls.sql
      - ../db/changelog/004-seed.sql
```

Read `db/changelog/` first to confirm the exact filenames listed above are current (this plan was written against the changelog state as of Phase 1-2 — check for any later-appended changesets like `002b-audit-log-sequence-grant.sql`/`002c-app-pool-noinherit.sql` mentioned in this project's own history, and include every actual `.sql` file present in `db/changelog/`, not just the four named above if more exist).

Note: `namespace: financial-mcp` in this kustomization sets the namespace on every resource it manages, which makes the explicit `namespace: financial-mcp` already present in each individual manifest's `metadata.namespace` redundant but not conflicting (Kustomize's namespace transformer is idempotent against an already-matching value) — leave the existing per-file `namespace:` fields as-is, do not strip them, to keep `kubectl apply -f k8s/NN-file.yaml` (non-Kustomize, direct-file apply) still working standalone for anyone who reads a single file in isolation.

- [ ] **Step 2: Verify `kubectl kustomize k8s` builds without error**

Run: `kubectl kustomize k8s > /tmp/kustomize-output.yaml && echo OK`
Expected: exits 0, produces a single multi-document YAML stream. Skim it for exactly 2 `ConfigMap` objects plus every resource from the 7 listed files (Secrets, Deployments, Services, the Job, the Namespace).

- [ ] **Step 3: Verify the generated ConfigMaps are byte-identical to Phase 4's**

The Phase 4 kind cluster should still be running from the prior phase's work. Compare:
```bash
kubectl get configmap keycloak-realm-export -n financial-mcp -o jsonpath='{.data.realm\.json}' > /tmp/live-realm.json
grep -A2 "kind: ConfigMap" /tmp/kustomize-output.yaml  # locate the generated keycloak-realm-export block, extract its realm.json value, compare
diff <(kubectl get configmap keycloak-realm-export -n financial-mcp -o jsonpath='{.data.realm\.json}') <(cat keycloak/realm-export.json)
```
Expected: no diff (the live cluster's `ConfigMap` data and the raw source file are identical, and by construction Kustomize's generator reads that same raw source file — so if this diff is clean, Kustomize's output is provably correct without needing fragile YAML-stream parsing). Do the equivalent check for each of the 6 keys in `liquibase-changelog` against their source files in `db/`.

- [ ] **Step 4: Commit**

```bash
git add k8s/kustomization.yaml
git commit -m "Add k8s/kustomization.yaml: declarative ConfigMap generation for Argo CD

Replaces the imperative 'kubectl create configmap --from-file=...'
commands k8s/bootstrap.sh used to run before applying manifests --
Argo CD needs everything declarative in git, so the realm-export and
liquibase-changelog ConfigMaps are now generated by Kustomize's
configMapGenerator from the same source files, with the same keys
k8s/04-keycloak.yaml and k8s/05-liquibase-job.yaml already reference.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Argo CD install + single Application manifest

**Files:**
- Create: `argocd/install.sh`
- Create: `argocd/application.yaml`

**Interfaces:**
- Consumes: Task 1's `k8s/kustomization.yaml` (the `Application`'s `spec.source.path: k8s` relies on Kustomize auto-detection via that file's presence).
- Produces: a repeatable Argo CD install step and one `Application` resource that Task 3's `bootstrap.sh` applies.

- [ ] **Step 1: Write `argocd/install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Installing Argo CD..."
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "Waiting for argocd-server..."
kubectl rollout status deployment/argocd-server -n argocd --timeout=180s

echo "Argo CD installed. Initial admin password:"
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
echo ""
echo "Access the UI: kubectl port-forward svc/argocd-server -n argocd 8081:443"
```

`chmod +x argocd/install.sh`.

- [ ] **Step 2: Write `argocd/application.yaml`**

Read the repo's actual remote URL first (`git remote get-url origin`) to use the real value, not a placeholder — this plan was written assuming `git@github.com:someshjha/poc_mcp.git`, but Argo CD's `repoURL` needs the HTTPS form (`https://github.com/someshjha/poc_mcp.git`) for anonymous/public-repo cloning (SSH would require a configured credential secret, out of scope for this local demo per the spec's Non-goals). Confirm the repo is public (or note in the report if it's private, which would need an Argo CD repo-credentials Secret this plan does not cover — flag this rather than silently assuming).

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: financial-mcp
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/someshjha/poc_mcp.git
    targetRevision: claude/db-schema-rls
    path: k8s
  destination:
    server: https://kubernetes.default.svc
    namespace: financial-mcp
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

`targetRevision: claude/db-schema-rls` is the current working branch, documented in the README (Task 3) as something to change to `main` once this work is merged — do not hardcode `main` here since the branch isn't merged yet and pointing Argo CD at `main` would sync nothing relevant.

- [ ] **Step 3: Commit**

```bash
git add argocd/install.sh argocd/application.yaml
git commit -m "Add Argo CD install script and the single financial-mcp Application

One Application (not an app-of-apps split -- see the Phase 5 design
spec's rationale), Kustomize-sourced from k8s/, automated sync with
prune and self-heal enabled.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `argocd/bootstrap.sh` + README

**Files:**
- Create: `argocd/bootstrap.sh`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1's kustomization, Task 2's `argocd/install.sh` + `argocd/application.yaml`, Phase 4's `k8s/kind-config.yaml` and the same image-build steps `k8s/bootstrap.sh` already established.
- Produces: the Phase 5 one-command entry point and its README documentation, ready for Task 4's live verification.

- [ ] **Step 1: Write `argocd/bootstrap.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER_NAME="financial-mcp"

if ! kind get clusters | grep -qx "$CLUSTER_NAME"; then
  echo "Creating kind cluster '$CLUSTER_NAME'..."
  kind create cluster --config k8s/kind-config.yaml
else
  echo "kind cluster '$CLUSTER_NAME' already exists, reusing."
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if ! git diff --quiet "origin/$CURRENT_BRANCH" HEAD -- k8s keycloak/realm-export.json db 2>/dev/null; then
  echo "WARNING: local k8s/keycloak/db state differs from origin/$CURRENT_BRANCH -- Argo CD syncs from the pushed branch, not your working tree. Push first: git push -u origin $CURRENT_BRANCH" >&2
fi

echo "Building images..."
docker build -f mcp_server/Dockerfile -t poc-mcp/mcp-server:local .
docker build -f ui/Dockerfile -t poc-mcp/ui:local .
docker build -f db/liquibase.Dockerfile -t poc-mcp/liquibase:local db

echo "Loading images into kind..."
kind load docker-image poc-mcp/mcp-server:local --name "$CLUSTER_NAME"
kind load docker-image poc-mcp/ui:local --name "$CLUSTER_NAME"
kind load docker-image poc-mcp/liquibase:local --name "$CLUSTER_NAME"

argocd/install.sh

echo "Applying the financial-mcp Application..."
kubectl apply -f argocd/application.yaml

echo "Waiting for Argo CD to sync..."
if command -v argocd >/dev/null 2>&1; then
  # argocd CLI available -- not logged in by default in a fresh install, so
  # fall back to the same kubectl-polling approach either way for a
  # dependency-free default path; a human with the CLI logged in can also
  # just run: argocd app wait financial-mcp --health --timeout 180
  :
fi
for _ in $(seq 1 60); do
  SYNC="$(kubectl get application financial-mcp -n argocd -o jsonpath='{.status.sync.status}' 2>/dev/null || echo '')"
  HEALTH="$(kubectl get application financial-mcp -n argocd -o jsonpath='{.status.health.status}' 2>/dev/null || echo '')"
  [ "$SYNC" = "Synced" ] && [ "$HEALTH" = "Healthy" ] && break
  sleep 3
done
if [ "$SYNC" != "Synced" ] || [ "$HEALTH" != "Healthy" ]; then
  echo "ERROR: Application did not reach Synced/Healthy within 180s (sync=$SYNC health=$HEALTH). Check: kubectl get application financial-mcp -n argocd -o yaml" >&2
  exit 1
fi

echo ""
echo "Argo CD is syncing financial-mcp from git. Verify with:"
echo "  kubectl get application financial-mcp -n argocd"
echo "  pip install -r scripts/requirements.txt -r ui/requirements.txt"
echo "  python3 scripts/verify_scopes.py"
echo "  python3 scripts/verify_mcp_server.py"
echo "  python3 scripts/verify_ui.py"
echo "  java -jar karate/karate.jar karate/scoped_access.feature"
echo ""
echo "Argo CD UI: kubectl port-forward svc/argocd-server -n argocd 8081:443, then https://localhost:8081 (admin / see argocd/install.sh's output)"
echo "Teardown: kind delete cluster --name $CLUSTER_NAME"
```

`chmod +x argocd/bootstrap.sh`. Trace every command against the actual files Tasks 1-2 created before finalizing (same diligence Phase 4's Task 3 applied) — this plan's draft may have a subtly wrong path or object name; fix what you find and note it in your report.

- [ ] **Step 2: Add a Phase 5 section to `README.md`**

Read `README.md` in full first, match its established style. Update the Status line to add Phase 5. Add:

```markdown
## Phase 5 quickstart (Argo CD GitOps)

Requires everything Phase 4 needs, plus a pushed branch (Argo CD syncs from git, not your working tree).

\`\`\`bash
git push -u origin claude/db-schema-rls
argocd/bootstrap.sh
kubectl get application financial-mcp -n argocd
pip install -r scripts/requirements.txt -r ui/requirements.txt
python3 scripts/verify_scopes.py
python3 scripts/verify_mcp_server.py
python3 scripts/verify_ui.py
java -jar karate/karate.jar karate/scoped_access.feature
\`\`\`

`argocd/bootstrap.sh` creates (or reuses) the `kind` cluster, builds and loads the same three local images Phase 4 used, installs Argo CD, and applies a single `Application` that syncs everything in `k8s/` from this repo's `claude/db-schema-rls` branch -- change `targetRevision` in `argocd/application.yaml` to `main` once this work is merged. From here on, `kubectl apply` is no longer how you deploy: edit a manifest, commit, push, and Argo CD reconciles the cluster automatically (`syncPolicy.automated` with `selfHeal: true` -- it also reverts any manual `kubectl edit` drift back to what's in git).

Argo CD UI: `kubectl port-forward svc/argocd-server -n argocd 8081:443`, then open `https://localhost:8081` (username `admin`, password printed by `argocd/install.sh`).

Teardown: `kind delete cluster --name financial-mcp` (also removes Argo CD, which lives in the same cluster).
```

(Use real newlines/backticks -- the fence markers above are escaped only for this plan document.)

- [ ] **Step 3: Commit**

```bash
git add argocd/bootstrap.sh README.md
git commit -m "Add argocd/bootstrap.sh and Phase 5 README quickstart

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Push the branch and verify Argo CD GitOps end-to-end

**Files:** None created/modified beyond what live verification requires — this task proves Tasks 1-3 work together against a real Argo CD sync from the real git remote.

**Interfaces:**
- Consumes: everything from Tasks 1-3 exactly as committed, plus a real push to `origin`.
- Produces: a verification report proving Argo CD reconciles the cluster from git, including a live self-heal/drift-correction demonstration.

**IMPORTANT — this task pushes to a real remote (`origin`, a GitHub repo).** This is a side-effecting action beyond local-only work. Before running the `git push` in Step 1, stop and get explicit confirmation from the human operator that pushing `claude/db-schema-rls` to `origin` now is authorized (it has been pushed once before earlier in this project's history, but re-confirm — do not assume standing authorization for every subsequent push). If running as an unattended subagent that cannot itself ask a human, escalate this as a BLOCKED/NEEDS_CONTEXT report rather than pushing unprompted.

- [ ] **Step 1: Push the branch** (after confirmation per the note above)

```bash
git push -u origin claude/db-schema-rls
```

- [ ] **Step 2: Tear down any stray previous cluster to start clean, then bootstrap**

```bash
kind delete cluster --name financial-mcp 2>/dev/null || true
argocd/bootstrap.sh 2>&1 | tee /tmp/argocd-bootstrap-output.log
```
Expected: exits 0, reports `Synced`/`Healthy`.

- [ ] **Step 3: Run every verification script and Karate**

```bash
pip install -r scripts/requirements.txt -r ui/requirements.txt
python3 scripts/verify_scopes.py
python3 scripts/verify_mcp_server.py
python3 scripts/verify_ui.py
karate/download-karate.sh
java -jar karate/karate.jar karate/scoped_access.feature
```
Expected: same pass counts as Phase 4's direct-`kubectl-apply` deployment (check the actual current counts, don't assume a stale number).

- [ ] **Step 4: Demonstrate live GitOps reconciliation**

Make a small, clearly-labeled, reversible change to a tracked file under `k8s/` (e.g., add a comment or a harmless label to `k8s/07-ui.yaml`), commit it, push it, then watch `kubectl get application financial-mcp -n argocd -w` (or poll it) pick up and apply the change within Argo CD's default poll interval (~3 minutes, or trigger an immediate refresh with `kubectl patch application financial-mcp -n argocd --type merge -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'` if you want to avoid waiting). Confirm via `kubectl get <resource> -n financial-mcp -o yaml` that the change landed without any manual `kubectl apply`. Then revert the change, commit, push again, and confirm Argo CD reconciles back — leaving the repo and cluster in the same verified-good state this task started with.

- [ ] **Step 5: Demonstrate self-heal (drift correction)**

Manually drift the live cluster away from git without touching git at all — e.g. `kubectl scale deployment/ui -n financial-mcp --replicas=0` — then confirm Argo CD's `selfHeal: true` reverts it back to the git-specified `replicas: 1` on its own (again, `argocd.argoproj.io/refresh` annotation or a short wait), without any `kubectl apply`/`kubectl scale` correction from you. This is the concrete proof that `syncPolicy.automated.selfHeal` actually works, not just that initial sync works.

- [ ] **Step 6: Check pod/Application health, leave the cluster running**

```bash
kubectl get pods -n financial-mcp
kubectl get application financial-mcp -n argocd -o wide
```
Expected: all pods `Running`/`Completed`, 0 restarts; Application `Synced`/`Healthy`. Do not tear down the cluster.

- [ ] **Step 7: Write a verification report**

Write the full report (bootstrap output summary, exact counts, the GitOps push-and-reconcile proof, the self-heal proof, pod/Application status, and any bugs found + fixes applied) to this plan's SDD workspace report file (follow whatever path `scripts/sdd-workspace`/`task-brief` tooling established for this plan, matching the same not-committed-to-repo convention Phase 3 and Phase 4's Task reports used — only the ledger references it, no new committed doc). If you made any real bug-fix commits along the way, commit those separately with a clear message (Co-Authored-By trailer included).
