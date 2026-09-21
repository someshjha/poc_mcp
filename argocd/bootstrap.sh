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
if ! git diff --quiet "origin/$CURRENT_BRANCH" HEAD -- kustomization.yaml k8s keycloak/realm-export.json db 2>/dev/null; then
  echo "WARNING: local kustomization.yaml/k8s/keycloak/db state differs from origin/$CURRENT_BRANCH -- Argo CD syncs from the pushed branch, not your working tree. Push first: git push -u origin $CURRENT_BRANCH" >&2
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
