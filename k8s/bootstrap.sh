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

# A completed Job with an unchanged spec is a no-op on re-apply, so a
# re-run of this script wouldn't pick up new changelog files -- delete any
# prior run first so this script stays safe to re-run after changelog edits.
kubectl delete job liquibase-migrate -n "$NAMESPACE" --ignore-not-found
kubectl apply -f k8s/05-liquibase-job.yaml
echo "Waiting for Liquibase migration Job..."
kubectl wait --for=condition=complete job/liquibase-migrate -n "$NAMESPACE" --timeout=120s

kubectl apply -f k8s/04-keycloak.yaml
echo "Waiting for Keycloak..."
kubectl rollout status deployment/keycloak -n "$NAMESPACE" --timeout=180s
for _ in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/realms/financial-mcp)" = "200" ] && break
  sleep 2
done
if [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/realms/financial-mcp)" != "200" ]; then
  echo "ERROR: Keycloak did not start serving realm 'financial-mcp' within 120s (Deployment is Available, but the realm import may have failed -- check: kubectl logs -n $NAMESPACE deployment/keycloak)" >&2
  exit 1
fi

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
