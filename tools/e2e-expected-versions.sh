#!/usr/bin/env bash
# Prints the version each E2E version-wait target should report once <env> has
# synced this checkout: one "<chart> <info-url> <version>" line per target.
#
# The apps-deployed dispatch carries only the apps commit, not a version per
# project, and by the time it fires the release pipeline has already merged
# every chart bump here. So this checkout is the statement of intent: what
# ArgoCD rolls out is what Helm resolves from it.
#
# Resolution follows Helm's override order for image.tag: values-<env>.yaml,
# then values.yaml, then Chart.yaml appVersion. A digest suffix is dropped
# because services report the tag they were built with.
#
# Usage: bash tools/e2e-expected-versions.sh <targets-file> <env>
# Env:   CHARTS_DIR (default: charts)
# Exit codes: 0 = every target resolved; 1 = a target is invalid or
# unresolvable, or there are none; 2 = usage error.
set -uo pipefail

charts_dir="${CHARTS_DIR:-charts}"

# Anchored to the two-space indent so only the top-level image block's own tag
# matches, never a nested one. Same pattern as promote-prd.yml.
image_tag() {
  [ -f "$1" ] || return 0
  sed -n '/^image:/,/^[^[:space:]]/ s/^  tag:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' "$1" | head -1
}

app_version() {
  sed -n 's/^appVersion:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' "$1" | head -1
}

expected_version() {
  local dir="${charts_dir}/$1" env="$2" tag
  tag=$(image_tag "${dir}/values-${env}.yaml")
  [ -n "$tag" ] || tag=$(image_tag "${dir}/values.yaml")
  [ -n "$tag" ] || tag=$(app_version "${dir}/Chart.yaml")
  printf '%s' "${tag%%@*}"
}

main() {
  [ "$#" -eq 2 ] || { echo "usage: $(basename "$0") <targets-file> <env>" >&2; return 2; }
  local file="$1" env="$2" chart url extra version failed=0 count=0
  [[ "$env" =~ ^[a-z]+$ ]] || { echo "::error::invalid env '${env}'." >&2; return 2; }
  [ -f "$file" ] || { echo "::error::${file} not found." >&2; return 1; }

  while read -r chart url extra || [ -n "$chart" ]; do
    count=$((count + 1))
    if [[ ! "$chart" =~ ^[a-z0-9-]+$ ]] || [ -n "$extra" ] \
      || [[ ! "$url" =~ ^https://[a-z0-9.-]+/actuator/info$ ]]; then
      echo "::error::[${chart}] malformed entry in ${file}: expected '<chart> https://<host>/actuator/info'." >&2
      failed=$((failed + 1)); continue
    fi
    if [ ! -f "${charts_dir}/${chart}/Chart.yaml" ]; then
      echo "::error::[${chart}] ${charts_dir}/${chart}/Chart.yaml not found." >&2
      failed=$((failed + 1)); continue
    fi
    version=$(expected_version "$chart" "$env")
    if [[ ! "$version" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$ ]]; then
      echo "::error::[${chart}] no image tag or appVersion resolves for ${env}." >&2
      failed=$((failed + 1)); continue
    fi
    printf '%s %s %s\n' "$chart" "$url" "$version"
  done < <(tr -d '\r' < "$file" | sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d')

  if [ "$count" -eq 0 ]; then
    echo "::error::${file} lists no targets; a wait on nothing proves nothing." >&2
    return 1
  fi
  [ "$failed" -eq 0 ]
}

main "$@"
