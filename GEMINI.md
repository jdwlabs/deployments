# GEMINI.md

This file provides guidance to Gemini CLI when working in this repository.
For the canonical reference, see [CLAUDE.md](CLAUDE.md) — this file mirrors that content.

## Repository Overview

ArgoCD Application definitions and custom Helm charts for jdwlabs tenant deployments. Merging to `main` triggers ArgoCD auto-sync — never apply changes directly to the cluster.

### Structure

- `argocd/` — ArgoCD Application and ApplicationSet manifests
- `charts/` — Custom Helm charts
- `tools/` — Helper scripts

## Development Commands

```bash
helm lint charts/<name>               # Lint chart
helm template <release> charts/<name> # Render templates
argocd app get <name>                 # App status (read-only)
argocd app diff <name>                # Live vs desired diff (read-only)
kubectl get applications -n argocd    # List all ArgoCD apps (read-only)
```

## Agent Contract

- `argocd app sync` is NEVER run autonomously — merge to main triggers sync
- `kubectl apply/delete` are out of scope
- Read-only `kubectl get/describe/logs` are safe
- Never set `targetRevision` to a feature branch
- Never commit secrets — use ExternalSecrets + Vault
