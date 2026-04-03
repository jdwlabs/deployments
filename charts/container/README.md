# container Helm Chart

[![Chart Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fcontainer%2FChart.yaml&query=%24.appVersion&prefix=v&label=Chart)](https://github.com/jdwlabs/deployments/blob/main/charts/container/Chart.yaml)
[![Non Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fcontainer%2FChart.yaml&query=%24.appVersion&prefix=v&label=Non)](https://github.com/jdwlabs/deployments/blob/main/charts/container/values-non.yaml)
[![Prod Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fcontainer%2Fvalues-prd.yaml&query=%24.image.tag&prefix=v&label=Prod)](https://github.com/jdwlabs/deployments/blob/main/charts/container/values-prd.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

This Helm chart is used to deploy the `container` frontend in the JDW platform. It’s primarily consumed by Argo CD for
GitOps-based deployments across environments.

## Layout

- `Chart.yaml` – Chart metadata and versioning
- `templates/` – Kubernetes manifests (deployment, service, ingress, etc.)
- `values.yaml` – Base configuration
- `values-*.yaml` – Environment-specific overrides

## Deployment

Argo CD pulls in this chart directly from the repo and applies the appropriate `values-*.yaml` based on the target
environment.

To test locally:

```bash
helm upgrade --install container ./container -f values-non.yaml
```

To remove:

```bash
helm uninstall container
```
