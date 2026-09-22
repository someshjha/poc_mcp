#!/usr/bin/env bash
set -euo pipefail

echo "Installing Argo CD..."
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
# Server-side apply: the stock manifest's applicationsets.argoproj.io CRD schema
# exceeds kubectl's 262144-byte last-applied-configuration annotation limit under
# client-side `kubectl apply`, which aborts that one object (and, since it's a
# multi-doc apply, still applies everything else before exiting non-zero).
# --server-side avoids that annotation entirely.
kubectl apply --server-side --force-conflicts -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "Waiting for argocd-server..."
kubectl rollout status deployment/argocd-server -n argocd --timeout=180s

echo "Argo CD installed. Initial admin password:"
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
echo ""
echo "Access the UI: kubectl port-forward svc/argocd-server -n argocd 8081:443"
