# authui Helm Chart

[![Chart Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fauthui%2FChart.yaml&query=%24.appVersion&prefix=v&label=Chart)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/authui/Chart.yaml)
[![Dev Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fauthui%2FChart.yaml&query=%24.appVersion&prefix=v&label=Dev)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/authui/values-dev.yaml)
[![UAT Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fauthui%2FChart.yaml&query=%24.appVersion&prefix=v&label=UAT)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/authui/values-uat.yaml)
[![Non Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fauthui%2FChart.yaml&query=%24.appVersion&prefix=v&label=Non)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/authui/values-non.yaml)
[![Prod Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Frefs%2Fheads%2Fmain%2Fcharts%2Fauthui%2Fvalues-prd.yaml&query=%24.image.tag&prefix=v&label=Prod)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/authui/values-prd.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

This Helm chart is used to deploy the `authui` frontend in the JDW platform. It’s primarily consumed by Argo CD for
GitOps-based deployments across environments.

## 📁 Layout

- `Chart.yaml` – Chart metadata and versioning
- `templates/` – Kubernetes manifests (deployment, service, ingress, etc.)
- `values.yaml` – Base configuration
- `values-*.yaml` – Environment-specific overrides

## 🚀 Deployment

Argo CD pulls in this chart directly from the repo and applies the appropriate `values-*.yaml` based on the target
environment.

To test locally:

```bash
helm upgrade --install authui ./authui -f values-dev.yaml
```

To remove:

```bash
helm uninstall authui
```
