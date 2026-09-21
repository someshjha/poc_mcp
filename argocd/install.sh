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
