# AGENTS.md

Canonical context for AI agents (Claude Code, OpenAI Codex, Gemini CLI, GitHub Copilot, and others) working in this repository. `CLAUDE.md` and `GEMINI.md` are thin pointers to this file — make edits here.

## What This Repo Is

jdwlabs `deployments` defines what runs on the jdwlabs Kubernetes cluster. It contains per-environment app definitions and custom Helm charts for tenant services. It is the GitOps delivery layer — `platform` configures the cluster foundation, this repo defines the workloads.

## Repository Structure

- `argocd/<env>/config.yaml` — app definitions for that environment (`non`, `prd`), expanded into ArgoCD Applications by the platform ApplicationSet
- `charts/<name>/` — custom Helm charts for jdwlabs services (`values.yaml` base + `values-<env>.yaml` overrides)
- `charts/common/` — shared library chart consumed by every app chart
- `tools/` — repo tooling: `generate-index.py` renders the Helm chart index page pushed to GitHub Pages by CI

## Key Concepts

- **GitOps:** This repo is the source of truth for what is deployed. Merging to `main` = deploying to production. Never bypass this with direct kubectl/argocd apply.
- **ArgoCD Application CRs:** Each app entry in `argocd/<env>/config.yaml` describes an application ArgoCD should deploy, including source chart, target namespace, and values.
- **Helm charts in `charts/`:** Custom charts for services that don't have an upstream chart or need heavy customization. App charts consume the `common` library chart (`charts/common/`) for the shared workload scaffold via a `file://../common` dependency — run `helm dependency build charts/<name>` before `helm lint`/`helm template`.
- **ExternalSecrets:** All secrets are injected at runtime from Vault via ExternalSecrets Operator — this repo contains no secret values.

## Development Commands

### Validate Helm charts

```bash
helm dependency build charts/<chart-name>        # Fetch the file:// library dependency (needed once before lint/template)
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

1. Create the Helm chart under `charts/<chart-name>/` if it's a custom chart — consume the `common` library chart (see `charts/common/README.md`)
2. Run `helm dependency build charts/<chart-name>` then `helm lint charts/<chart-name>` to validate
3. Add an entry to each relevant `argocd/<env>/config.yaml` (`name`, `namespace`, `chartPath`, `valueFiles`)
4. Merge to main — ArgoCD picks it up automatically

### Debug a failed ArgoCD sync

1. `argocd app get <name>` — check health and sync status
2. `argocd app diff <name>` — see what has drifted from desired state
3. `kubectl describe application <name> -n argocd` — detailed events
4. Check ArgoCD UI for full error output

### Update a chart

1. Edit the chart under `charts/<chart-name>/` and bump `version` in its `Chart.yaml`
2. Adjust `values.yaml` / `values-<env>.yaml` as needed
3. Run `helm template` to verify the rendered output looks correct
4. Merge to main — ArgoCD will sync automatically

## Code & Manifest Comments

Never put a Jira ticket ID (`JDWLABS-*`) or PR/issue number in a comment in
any file here — app config, ArgoCD manifests, and Helm charts included.
Traceability lives in the commit message and PR description; comments
should explain *why* the config is what it is so they stay meaningful
after the ticket closes.

## Constraints

- `argocd app sync` is NEVER run autonomously — sync is triggered by Git merge
- NEVER hand-edit `charts/*/values-prd.yaml` — prd image tags change only via
  pull requests opened by the `Promote PRD` workflow (see `docs/prd-promotion.md`);
  `Chart.yaml` `version` is for chart packaging changes only, never image tags
- `kubectl apply` and `kubectl delete` are out of scope — workload management is ArgoCD's job
- Read-only `kubectl get`, `kubectl describe`, `kubectl logs` are safe
- `targetRevision` must always be `HEAD` or a semver tag — never a branch name other than the default
- No secrets in this repo — reference Vault paths via ExternalSecret manifests only

## References

- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [Helm Documentation](https://helm.sh/docs/)
