#!/usr/bin/env bash
# Tests for the Go services' version routes.
#
# The E2E gate reads each service's /actuator/info before testing. On the
# shared API host /actuator/* belongs to the JVM's catch-all, so each Go
# service answers that one path on a hostname of its own instead.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf 'FAIL %s\n     %s\n' "$1" "$2"; }

assert_eq() {
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "expected '$3', got '$2'"; fi
}

# Release names mirror argocd/<env>/config.yaml, which drive common.fullname.
render() {
  local chart="$1" env="$2"
  helm template "${chart}-${env}" "${ROOT}/charts/${chart}" \
    -f "${ROOT}/charts/${chart}/values.yaml" \
    -f "${ROOT}/charts/${chart}/values-${env}.yaml"
}

for chart in identity-service profile-service; do
  if ! helm dependency build "${ROOT}/charts/${chart}" >/dev/null 2>&1; then
    no "${chart}: dependencies build" "helm dependency build failed"
    continue
  fi

  non=$(render "$chart" non)
  route=$(printf '%s\n' "$non" | yq "select(.kind == \"HTTPRoute\" and .metadata.name == \"${chart}-non-version\")")

  assert_eq "${chart} non: version route exists" \
    "$(printf '%s\n' "$route" | yq '.metadata.name')" "${chart}-non-version"
  assert_eq "${chart} non: version route answers on the service's own hostname only" \
    "$(printf '%s\n' "$route" | yq -o=json -I=0 '.spec.hostnames')" "[\"${chart}.non.jdwlabs.com\"]"
  assert_eq "${chart} non: version route has exactly one rule" \
    "$(printf '%s\n' "$route" | yq '.spec.rules | length')" "1"
  assert_eq "${chart} non: the rule matches only GET /actuator/info" \
    "$(printf '%s\n' "$route" | yq -o=json -I=0 '.spec.rules[0].matches')" \
    '[{"path":{"type":"Exact","value":"/actuator/info"},"method":"GET"}]'
  assert_eq "${chart} non: the rule targets the chart's own Service" \
    "$(printf '%s\n' "$route" | yq -o=json -I=0 '.spec.rules[0].backendRefs')" \
    "[{\"name\":\"${chart}-non\",\"port\":8080}]"

  shared=$(printf '%s\n' "$non" | yq 'select(.kind == "HTTPRoute") | select(.spec.rules[].matches[].path.value == "/actuator/info") | select(.spec.hostnames[] == "usersrole.non.jdwlabs.com") | .metadata.name')
  assert_eq "${chart} non: nothing takes /actuator/info from the JVM on the shared host" "$shared" ""

  prd=$(render "$chart" prd)
  assert_eq "${chart} prd: no version route" \
    "$(printf '%s\n' "$prd" | yq 'select(.kind == "HTTPRoute" and .metadata.name == "'"${chart}"'-prd-version") | .metadata.name')" ""

  if out=$(helm template "${chart}-non" "${ROOT}/charts/${chart}" --set versionRoute.enabled=true 2>&1); then
    no "${chart}: enabling the route without a host fails the render" "render succeeded"
  else
    case "$out" in
      *"versionRoute.host is required"*) ok "${chart}: enabling the route without a host fails the render" ;;
      *) no "${chart}: enabling the route without a host fails the render" "unexpected error: $out" ;;
    esac
  fi
done

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
