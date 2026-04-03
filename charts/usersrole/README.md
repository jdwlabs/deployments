# usersrole Helm Chart

[![Chart Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fusersrole%2FChart.yaml&query=%24.appVersion&prefix=v&label=Chart)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/usersrole/Chart.yaml)
[![Dev Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fusersrole%2FChart.yaml&query=%24.appVersion&prefix=v&label=Dev)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/usersrole/values-dev.yaml)
[![UAT Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fusersrole%2FChart.yaml&query=%24.appVersion&prefix=v&label=UAT)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/usersrole/values-uat.yaml)
[![Non Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fusersrole%2FChart.yaml&query=%24.appVersion&prefix=v&label=Non)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/usersrole/values-non.yaml)
[![Prod Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fusersrole%2Fvalues-prd.yaml&query=%24.image.tag&prefix=v&label=Prod)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/usersrole/values-prd.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

The `usersrole` Helm chart is used to deploy the backend service responsible for managing the relationship between users and roles in the JDW platform. It is part of the core authorization and identity management stack.

## 📁 Layout

- `Chart.yaml` – Chart metadata and versioning
- `templates/` – Kubernetes manifests:
    - `deployment.yaml` – Main deployment
    - `externalsecrets.yaml` – Integration with secret management
    - `service.yaml`, `ingress.yaml` – Service exposure
    - `hpa.yaml` – Autoscaling
    - `serviceaccount.yaml`, `_helpers.tpl`, `NOTES.txt` – Operational components
    - `tests/test-connection.yaml` – Basic connectivity test
- `values.yaml` – Base configuration shared across environments
- `values-*.yaml` – Environment-specific overrides

## 🚀 Deployment

This chart is managed and deployed by Argo CD using GitOps workflows. Each environment references the appropriate values file.

To test locally:

```bash
helm upgrade --install usersrole ./usersrole -f values-dev.yaml
```

To uninstall:

```bash
helm uninstall usersrole
```
