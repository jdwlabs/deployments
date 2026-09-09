# identity-service Helm Chart

[![Chart Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fidentity-service%2FChart.yaml&query=%24.appVersion&prefix=v&label=Chart)](https://github.com/jdwlabs/deployments/blob/main/charts/identity-service/Chart.yaml)
[![Non Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fidentity-service%2Fvalues-non.yaml&query=%24.image.tag&prefix=v&label=Non)](https://github.com/jdwlabs/deployments/blob/main/charts/identity-service/values-non.yaml)
[![Prod Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fidentity-service%2Fvalues-prd.yaml&query=%24.image.tag&prefix=v&label=Prod)](https://github.com/jdwlabs/deployments/blob/main/charts/identity-service/values-prd.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Deploys `identity-service`, the Go half of the `usersrole` split that serves the
`/auth`, `/api/users` and `/api/roles` operations against the same `auth` schema
the JVM service uses.

## Routing is off

`ingress.enabled` is `false` in every environment. The workload runs, is
scraped and proves itself healthy while taking no user traffic; `usersrole`
still serves every request. Turning routing on is a separate change so that it
is small enough to read and to revert.

When it lands it will not need a hostname of its own: the frontends resolve
every API group through one `AUTH_BASE_URL`, so this chart claims its paths on
the `usersrole` hostname through `ingress.rules` in the
[`common` library chart](../common/README.md), and the Gateway prefers the more
specific match.

## Shared with usersrole, on purpose

- **One database role and one connection URL.** `UR_PG_DATASOURCE_URL`,
  `UR_PG_USERNAME` and `UR_PG_PASSWORD` are the variables the JVM service
  reads, pointing at the same cluster and database. The Go service accepts the
  JDBC form and rewrites it for its own driver.
- **One signing secret.** `UR_JWT_SECRET_KEY` resolves to the same vault
  property `usersrole` reads. All three services verify each other's tokens, so
  a second key would mint tokens the others refuse.
- **One issuer origin.** `ID_JWT_ISSUER_ORIGIN` names the host the API is
  reached through, not this service, and must equal `profile-service`'s
  `PS_JWT_ISSUER_ORIGIN`.

## Layout

- `Chart.yaml` – Chart metadata and versioning
- `templates/` – Kubernetes manifests:
    - `deployment.yaml` – Main deployment
    - `externalsecrets.yaml` – Postgres credentials and the JWT signing secret
    - `service.yaml`, `httproute.yaml` – Service exposure
    - `hpa.yaml` – Autoscaling
    - `servicemonitor.yaml` – Prometheus scrape configuration
    - `serviceaccount.yaml`, `NOTES.txt` – Operational components
    - `tests/test-connection.yaml` – Basic connectivity test
- `values.yaml` – Base configuration shared across environments
- `values-*.yaml` – Environment-specific overrides

## Shared Scaffold

Scaffold templates (deployment, service, service account, HPA, HTTPRoute, tests) are one-line includes of the
[`common` library chart](../common/README.md), declared as a `file://../common` dependency in `Chart.yaml`. Run
`helm dependency build charts/identity-service` once after cloning before linting or templating locally.

## Deployment

This chart is managed and deployed by Argo CD using GitOps workflows. Each environment references the appropriate values file.

To test locally:

```bash
helm upgrade --install identity-service ./identity-service -f values-non.yaml
```

To uninstall:

```bash
helm uninstall identity-service
```
