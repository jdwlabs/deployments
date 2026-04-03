# secret-store Helm Chart

[![Chart Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fsecret-store%2FChart.yaml&query=%24.appVersion&prefix=v&label=Chart)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/secret-store/Chart.yaml)
[![Dev Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fsecret-store%2FChart.yaml&query=%24.appVersion&prefix=v&label=Dev)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/secret-store/values-dev.yaml)
[![UAT Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fsecret-store%2FChart.yaml&query=%24.appVersion&prefix=v&label=UAT)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/secret-store/values-uat.yaml)
[![Non Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fsecret-store%2FChart.yaml&query=%24.appVersion&prefix=v&label=Non)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/secret-store/values-non.yaml)
[![Prod Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fsecret-store%2FChart.yaml&query=%24.appVersion&prefix=v&label=Prod)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/secret-store/values-prd.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

This Helm chart is used to deploy the `secret-store` components in the JDW platform. It manages Kubernetes and Vault-backed
secret providers, and is primarily consumed by Argo CD for GitOps-based deployments.

## 📁 Layout

- `Chart.yaml` – Chart metadata and versioning
- `templates/` – Kubernetes manifests:
    - `k8s-secretstore.yaml` – SecretStore using native K8s secrets
    - `vault-secretstore.yaml` – SecretStore backed by Vault
    - `clusterrole.yaml`, `clusterrolebinding.yaml` – RBAC setup
    - `serviceaccount.yaml` – Service account for workload identity
- `values.yaml` – Base configuration
- `values-*.yaml` – Environment-specific overrides

## 🚀 Deployment

Argo CD applies this chart using the appropriate `values-*.yaml` for each environment.

To test locally:

```bash
helm upgrade --install secret-store ./secret-store -f values-dev.yaml
```

To remove:

```bash
helm uninstall secret-store
```
