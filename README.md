# Deployments

[![CI](https://github.com/jdwlabs/deployments/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/jdwlabs/deployments/actions/workflows/ci.yaml)
[![Release](https://github.com/jdwlabs/deployments/actions/workflows/release.yaml/badge.svg?branch=main)](https://github.com/jdwlabs/deployments/actions/workflows/release.yaml)
[![License](https://img.shields.io/badge/License-PolyForm%20NonCommercial%201.0-blue)](https://polyformproject.org/licenses/noncommercial/1.0.0/)

GitOps deployment repository for the `jdwlabs` tenant on the Jdwlabs platform. ArgoCD reads this repo via the
`jdwlabs-deployments` ApplicationSet to deploy applications into tenant-owned namespaces.

## Repository Structure

```
deployments/
├── argocd/
│   ├── non/
│   │   └── config.yaml  # Defines apps for non-prod environment
│   └── prd/
│       └── config.yaml  # Defines apps for prod environment
└── charts/
    ├── common/                # Library chart: shared workload scaffold
    └── <chart-name>/
        ├── Chart.yaml         # Declares the file://../common dependency
        ├── templates/         # One-line includes of common.* + app-specific extras
        ├── values.yaml        # Base values
        ├── values-non.yaml    # Non-prod overrides
        └── values-prd.yaml    # Prod overrides
```

## How It Works

The platform's `jdwlabs-deployments` ApplicationSet uses a matrix generator that:

1. Scans `argocd/*/config.yaml` (one file per environment)
2. Expands the `apps` array from each config file
3. Creates an ArgoCD Application for each entry, named `jdwlabs-<name>`

All apps use automated sync with prune, self-heal, `ServerSideApply`, and `PruneLast`.

## Versioning & prd Promotion

Per chart, three version fields have distinct owners — see
[docs/prd-promotion.md](docs/prd-promotion.md) for the full contract:

- `Chart.yaml` `version` — chart **packaging** only (templates/values
  structure/dependencies). Never mirror an image tag into it; it drives the
  Helm repo release stream.
- `Chart.yaml` `appVersion` — what **non** runs; maintained by the apps-repo
  release bot via direct commit.
- `values-prd.yaml` `image.tag` — what **prd** runs; changed **only** by
  pull requests opened by the `Promote PRD` workflow after a passing non E2E
  run (or a manual dispatch). Never hand-edit it. prd-path changes require a
  code-owner review to merge.

## Config Schema

Each `argocd/<env>/config.yaml` contains:

```yaml
apps:
  - name: <app-name>                # Unique name for the application
    namespace: <tenant>-<ns>        # Must be a namespace the tenant owns
    chartPath: charts/<chart-name>  # Path to chart in the deployment repo
    syncWave: "0"                   # Default ordering (default: "0")
    valueFiles:
      - values.yaml
      - values-<env>.yaml
```

| Field        | Required | Description                                                  |
|--------------|----------|--------------------------------------------------------------|
| `name`       | Yes      | Unique name for the application (used in ArgoCD Application) |
| `namespace`  | Yes      | Target namespace for deployment (must be owned by tenant)    |
| `chartPath`  | Yes      | Path to the Helm chart within the deployment repo            |
| `syncWave`   | No       | Sync wave for ordering (default: "0")                        |
| `valueFiles` | yes      | List of values files to use (base + environment-specific)    |

## Adding a New App

1. Create the Helm chart under `charts/<name>/` — consume the `common` library chart for the standard
   scaffold (see `charts/common/README.md`)
2. Add base `values.yaml` and per-environment `values-<env>.yaml` files
3. Add an entry to each relevant `argocd/<env>/config.yaml`
4. Commit and push to `main`

## Adding a New Environment

1. Create `argocd/<env>/config.yaml` with the app definitions
2. Add `values-<env>.yaml` files for each chart as needed
3. Ensure the namespace exists in the platform tenant config (`tenants/jdwlabs/tenant.yaml`)

## Environments

| Environment | Namespace     | Config                   |
|-------------|---------------|--------------------------|
| non         | `jdwlabs-non` | `argocd/non/config.yaml` | 
| prd         | `jdwlabs-prd` | `argocd/prd/config.yaml` |

## Sync Wave Ordering

| Wave | Charts                 |
|------|------------------------|
| 1    | All application charts |

## Prerequisites

The following must exist in the cluster before deploying (all managed by the platform repo):

- Tenant namespaces provisioned by the platform
- External Secrets Operator (ESO) installed
- `vault` ClusterSecretStore (platform-managed, reads from Vault)
- `k8s-secret-store` ClusterSecretStore (platform-managed, reads from `database` namespace)
- cert-manager with `letsencrypt-prod` ClusterIssuer
- nginx ingress controller
- CNPG PostgreSQL clusters in the `database` namespace

Secret stores are managed centrally by the platform - no per-tenant secret store setup is needed.
