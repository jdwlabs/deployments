# AGENTS.md

Context for AI agents (OpenAI Codex, GitHub Copilot, and others) working in this repository.

## What This Repo Is

jdwlabs `deployments` defines what runs on the jdwlabs Kubernetes cluster. It contains ArgoCD Application manifests and custom Helm charts for tenant services. It is the GitOps delivery layer — `platform` configures the cluster foundation, this repo defines the workloads.

## Key Concepts

- **GitOps:** This repo is the source of truth for what is deployed. Merging to `main` = deploying to production. Never bypass this with direct kubectl/argocd apply.
- **ArgoCD Application CRs:** Each app entry in `argocd/<env>/config.yaml` describes an application ArgoCD should deploy, including source chart, target namespace, and values.
- **Helm charts in `charts/`:** Custom charts for services that don't have an upstream chart or need heavy customization. App charts consume the `common` library chart (`charts/common/`) for the shared workload scaffold via a `file://../common` dependency — run `helm dependency build charts/<name>` before `helm lint`/`helm template`.
- **ExternalSecrets:** All secrets are injected at runtime from Vault via ExternalSecrets Operator — this repo contains no secret values.

## Key Files

- `argocd/<env>/config.yaml` — app definitions for that environment, expanded into ArgoCD Applications by the platform ApplicationSet
- `charts/<name>/Chart.yaml` — Helm chart metadata (declares the `file://../common` dependency)
- `charts/<name>/templates/` — Kubernetes manifest templates (one-line includes of `common.*` plus app-specific extras)
- `charts/common/templates/` — shared library templates included by every app chart

## Constraints

- Do not run `argocd app sync` — sync is triggered by Git merge
- Do not run `kubectl apply/delete` — workload management is ArgoCD's job
- `targetRevision` must always be `HEAD` or a semver tag — never a branch name other than the default
- No secrets in this repo — reference Vault paths via ExternalSecret manifests only
