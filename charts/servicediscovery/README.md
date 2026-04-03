# servicediscovery Helm Chart

[![Chart Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fservicediscovery%2FChart.yaml&query=%24.appVersion&prefix=v&label=Chart)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/servicediscovery/Chart.yaml)
[![Dev Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fservicediscovery%2FChart.yaml&query=%24.appVersion&prefix=v&label=Dev)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/servicediscovery/values-dev.yaml)
[![UAT Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fservicediscovery%2FChart.yaml&query=%24.appVersion&prefix=v&label=UAT)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/servicediscovery/values-uat.yaml)
[![Non Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fservicediscovery%2FChart.yaml&query=%24.appVersion&prefix=v&label=Non)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/servicediscovery/values-non.yaml)
[![Prod Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwillmsen%2Fjdw-apps%2Fmain%2Fcharts%2Fservicediscovery%2Fvalues-prd.yaml&query=%24.image.tag&prefix=v&label=Prod)](https://github.com/jdwillmsen/jdw-apps/blob/main/charts/servicediscovery/values-prd.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

This Helm chart is used to deploy the `servicediscovery` backend service in the JDW platform. It exposes dynamic service
metadata and is primarily consumed by other micro frontends or backend components at runtime. Argo CD handles deployment
across environments.

## 📁 Layout

- `Chart.yaml` – Chart metadata and versioning
- `templates/` – Kubernetes manifests:
    - `deployment.yaml` – Service deployment configuration
    - `service.yaml`, `ingress.yaml` – Service exposure
    - `configmap.yaml` – Runtime service config
    - `externalsecrets.yaml` – Secrets managed via External Secrets
    - `hpa.yaml` – Horizontal pod autoscaler
    - `serviceaccount.yaml`, `NOTES.txt`, `tests/` – Operational details
- `values.yaml` – Base configuration
- `values-*.yaml` – Environment-specific overrides

## 🚀 Deployment

Argo CD automatically deploys this chart using the appropriate values file depending on the environment.

To test locally:

```bash
helm upgrade --install servicediscovery ./servicediscovery -f values-dev.yaml
```

To remove:

```bash
helm uninstall servicediscovery
```
