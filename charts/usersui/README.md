# usersui Helm Chart

[![Chart Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fusersui%2FChart.yaml&query=%24.appVersion&prefix=v&label=Chart)](https://github.com/jdwlabs/deployments/blob/main/charts/usersui/Chart.yaml)
[![Non Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fusersui%2FChart.yaml&query=%24.appVersion&prefix=v&label=Non)](https://github.com/jdwlabs/deployments/blob/main/charts/usersui/values-non.yaml)
[![Prod Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fusersui%2Fvalues-prd.yaml&query=%24.image.tag&prefix=v&label=Prod)](https://github.com/jdwlabs/deployments/blob/main/charts/usersui/values-prd.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

The `usersui` Helm chart is used to deploy the frontend application for managing users in the JDW platform. It's designed to integrate with the rest of the platform's identity and access management stack.

## Layout

- `Chart.yaml` – Chart metadata and versioning
- `templates/` – Kubernetes manifests:
    - `deployment.yaml` – Main deployment
    - `service.yaml`, `ingress.yaml` – Service exposure
    - `hpa.yaml` – Autoscaling setup
    - `serviceaccount.yaml`, `_helpers.tpl`, `NOTES.txt` – Support resources
    - `tests/test-connection.yaml` – Helm test for connectivity
- `values.yaml` – Base configuration
- `values-dev.yaml`, `values-uat.yaml`, `values-non.yaml`, `values-prd.yaml` – Environment-specific overrides

## Deployment

This chart is deployed via Argo CD and supports GitOps-driven deployments across all environments.

To test locally:

```bash
helm upgrade --install usersui ./usersui -f values-non.yaml
```

To remove:

```bash
helm uninstall usersui
```
