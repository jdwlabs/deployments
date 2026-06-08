# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## Repository Overview

ArgoCD Application definitions and custom Helm charts for jdwlabs tenant service deployments. This repository is watched by ArgoCD — merging to `main` triggers automatic synchronization.

### Structure

- `argocd/` — ArgoCD Application and ApplicationSet manifests
- `charts/` — Custom Helm charts for jdwlabs services
- `tools/` — Deployment helper scripts

### Tech Stack

- **GitOps controller:** ArgoCD
- **Packaging:** Helm
- **Orchestration:** Kubernetes

## Development Commands

### Validate Helm charts

```bash
helm lint charts/<chart-name>                    # Lint chart for errors
helm template <release> charts/<chart-name>      # Render templates locally
helm template <release> charts/<chart-name> --debug  # Verbose render with values
```

### Inspect ArgoCD state (read-only)

```bash
argocd app get <app-name>                        # App health and sync status
argocd app diff <app-name>                       # Live vs desired state diff
argocd app list                                  # All apps and their status
```

### Inspect Kubernetes state (read-only)

```bash
kubectl get applications -n argocd               # All ArgoCD Application CRs
kubectl describe application <name> -n argocd    # Detailed app status and events
kubectl get pods -n <namespace>                  # Pod status for a namespace
kubectl logs <pod> -n <namespace>                # Pod logs
```

## Common Tasks

### Add a new application deployment

1. Add the Helm chart to `charts/<chart-name>/` if it's a custom chart
2. Run `helm lint charts/<chart-name>` to validate
3. Create an ArgoCD Application manifest in `argocd/` pointing to the chart
4. Merge to main — ArgoCD picks it up automatically

### Debug a failed ArgoCD sync

1. `argocd app get <name>` — check health and sync status
2. `argocd app diff <name>` — see what has drifted from desired state
3. `kubectl describe application <name> -n argocd` — detailed events
4. Check ArgoCD UI for full error output

### Update a chart version

1. Edit the chart version in `argocd/<app>/values.yaml` or the Application manifest
2. Run `helm template` to verify the rendered output looks correct
3. Merge to main — ArgoCD will sync automatically

## AI Agent Contract

- `argocd app sync` is NEVER run autonomously — sync happens via GitOps on merge to main
- `kubectl apply` and `kubectl delete` are out of scope
- Read-only `kubectl get`, `kubectl describe`, `kubectl logs` are safe
- Never set ArgoCD Application `targetRevision` to a feature branch — always `HEAD` or a semver tag
- Never commit secrets — all secrets come from Vault via ExternalSecrets Operator

## References

- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [Helm Documentation](https://helm.sh/docs/)
