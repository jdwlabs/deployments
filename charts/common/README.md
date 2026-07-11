# common Helm Library Chart

[![Chart Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjdwlabs%2Fdeployments%2Frefs%2Fheads%2Fmain%2Fcharts%2Fcommon%2FChart.yaml&query=%24.version&prefix=v&label=Chart)](https://github.com/jdwlabs/deployments/blob/main/charts/common/Chart.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Helm **library** chart providing the shared workload scaffold for every jdwlabs app chart. It renders nothing on its
own — app charts include its named templates so a template fix lands once here and propagates to all apps.

## Provided templates

| Template                    | Renders                                     |
|-----------------------------|---------------------------------------------|
| `common.deployment`         | `apps/v1` Deployment                        |
| `common.service`            | `v1` Service                                |
| `common.serviceaccount`     | `v1` ServiceAccount (when `serviceAccount.create`) |
| `common.hpa`                | `autoscaling/v2` HorizontalPodAutoscaler (when `autoscaling.enabled`) |
| `common.httproute`          | Gateway API HTTPRoute (when `ingress.enabled`) |
| `common.testConnection`     | `helm.sh/hook: test` connection-test Pod    |
| `common.notes`              | Post-install NOTES output                   |

Helper templates: `common.name`, `common.fullname`, `common.chart`, `common.labels`, `common.selectorLabels`,
`common.serviceAccountName`. Labels are computed from the *consuming* chart's `Chart.yaml`, so each app keeps its own
`helm.sh/chart` and `app.kubernetes.io/version` labels.

## Consuming the chart

App `Chart.yaml`:

```yaml
dependencies:
  - name: common
    version: 0.1.0
    repository: file://../common
```

App template files are one-line includes, e.g. `templates/deployment.yaml`:

```yaml
{{ include "common.deployment" . }}
```

Run `helm dependency build charts/<app>` after cloning (CI does this automatically). The vendored
`charts/<app>/charts/*.tgz` is gitignored; `Chart.lock` is committed.

App-specific templates (`externalsecrets.yaml`, `configmap.yaml`) stay in the owning app chart — the library only
carries scaffold shared by all apps.

## Values contract

The library reads the same values every app chart already defines: `replicaCount`, `autoscaling`,
`revisionHistoryLimit`, `image`, `imagePullSecrets`, `nameOverride`, `fullnameOverride`, `serviceAccount`,
`podAnnotations`, `podLabels`, `podSecurityContext`, `securityContext`, `service`, `ingress`, `resources`,
`livenessProbe`, `readinessProbe`, `volumes`, `volumeMounts`, `nodeSelector`, `tolerations`, `affinity`, `envs`,
`envSecrets`.

Notable semantics:

- `replicaCount` — only applied when `autoscaling.enabled` is `false`; every environment currently autoscales, so the
  HPA owns the replica count and this value is inert. It defaults to `1` in app charts so disabling autoscaling never
  scales a workload to zero by accident.
- `extraEnv` — list of `{name, value}` pairs rendered *before* `envs`/`envSecrets`; values pass through `tpl`, so apps
  can inject chart-derived data, e.g. `value: '{{ .Values.image.tag | default .Chart.AppVersion }}'`.
- `service.targetPort` — optional; when unset the Service targets the named container port `http` (which the shared
  Deployment binds to `service.port`), so both forms hit the same port.
- `envSecrets` — list of `{secretKey, secretName}` (plus `key`/`property` consumed by per-app ExternalSecret
  templates); each entry becomes an env var sourced from a Kubernetes Secret.

## Making a shared template change

1. Edit the template here and bump `version` in `charts/common/Chart.yaml`.
2. Update the `dependencies` pin in each app `Chart.yaml` and run `helm dependency update charts/<app>` to refresh
   `Chart.lock`.
3. Merge — ArgoCD builds the `file://` dependency from the repo at sync time, so all apps pick up the fix.

Publication to the gh-pages Helm repo happens through the existing tag-driven release workflow: tag `common-v<version>`
after merging.
