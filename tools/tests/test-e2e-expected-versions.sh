#!/usr/bin/env bash
# Tests for tools/e2e-expected-versions.sh.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${ROOT}/tools/e2e-expected-versions.sh"

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf 'FAIL %s\n     %s\n' "$1" "$2"; }

assert_eq() {
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "expected '$3', got '$2'"; fi
}

assert_contains() {
  case "$2" in
    *"$3"*) ok "$1" ;;
    *) no "$1" "expected output to contain '$3', got: $2" ;;
  esac
}

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
CHARTS="$WORK_DIR/charts"
DIGEST="sha256:$(printf 'a%.0s' $(seq 64))"

chart() {
  local name="$1" app="$2"
  mkdir -p "$CHARTS/$name"
  printf 'apiVersion: v2\nname: %s\nversion: 0.1.0\nappVersion: "%s"\n' "$name" "$app" > "$CHARTS/$name/Chart.yaml"
  printf 'image:\n  repository: jdwlabs/%s\n  tag: ""\n' "$name" > "$CHARTS/$name/values.yaml"
}

# Overlay pins a digest: the service reports the tag, never the digest.
chart pinned 9.9.9
printf 'image:\n  # a comment inside the block\n  tag: "0.0.3@%s"\n' "$DIGEST" > "$CHARTS/pinned/values-non.yaml"
# Overlay sets no tag and base tag is empty: appVersion wins.
chart tracking 1.3.5
printf 'image:\n  pullPolicy: Always\nsidecar:\n  image:\n    tag: "nested-ignored"\n' > "$CHARTS/tracking/values-non.yaml"
# No overlay at all, base values pin a tag.
chart basepinned 9.9.9
printf 'image:\n  repository: jdwlabs/basepinned\n  tag: "2.0.0"\n' > "$CHARTS/basepinned/values.yaml"

targets="$WORK_DIR/targets"
printf '# comment\r\npinned https://pinned.non.jdwlabs.com/actuator/info\r\n\r\ntracking https://tracking.non.jdwlabs.com/actuator/info\nbasepinned https://b.non.jdwlabs.com/actuator/info\n' > "$targets"

out=$(CHARTS_DIR="$CHARTS" bash "$SCRIPT" "$targets" non); rc=$?
assert_eq "resolves: exits zero" "$rc" "0"
assert_eq "resolves: digest dropped, appVersion fallback, base tag fallback, CRLF and comments ignored" "$out" "$(printf '%s\n%s\n%s' \
  'pinned https://pinned.non.jdwlabs.com/actuator/info 0.0.3' \
  'tracking https://tracking.non.jdwlabs.com/actuator/info 1.3.5' \
  'basepinned https://b.non.jdwlabs.com/actuator/info 2.0.0')"

printf 'pinned https://pinned.non.jdwlabs.com/actuator/info\ntracking https://tracking.non.jdwlabs.com/actuator/info' > "$WORK_DIR/t-nonl"
out=$(CHARTS_DIR="$CHARTS" bash "$SCRIPT" "$WORK_DIR/t-nonl" non); rc=$?
assert_eq "no trailing newline: exits zero" "$rc" "0"
assert_eq "no trailing newline: the last target is still printed" "$out" "$(printf '%s\n%s' \
  'pinned https://pinned.non.jdwlabs.com/actuator/info 0.0.3' \
  'tracking https://tracking.non.jdwlabs.com/actuator/info 1.3.5')"

printf 'missing https://m.non.jdwlabs.com/actuator/info\n' > "$WORK_DIR/t-missing"
out=$(CHARTS_DIR="$CHARTS" bash "$SCRIPT" "$WORK_DIR/t-missing" non 2>&1); rc=$?
assert_eq "unknown chart: exits one" "$rc" "1"
assert_contains "unknown chart: says so" "$out" "::error::[missing]"

printf 'pinned http://pinned.non.jdwlabs.com/actuator/info\n' > "$WORK_DIR/t-http"
out=$(CHARTS_DIR="$CHARTS" bash "$SCRIPT" "$WORK_DIR/t-http" non 2>&1); rc=$?
assert_eq "non-https or non-info URL: exits one" "$rc" "1"

printf 'pinned https://p.non.jdwlabs.com/actuator/info extra\n' > "$WORK_DIR/t-extra"
out=$(CHARTS_DIR="$CHARTS" bash "$SCRIPT" "$WORK_DIR/t-extra" non 2>&1); rc=$?
assert_eq "extra field: exits one" "$rc" "1"

printf '# nothing\n' > "$WORK_DIR/t-empty"
out=$(CHARTS_DIR="$CHARTS" bash "$SCRIPT" "$WORK_DIR/t-empty" non 2>&1); rc=$?
assert_eq "no targets: exits one (an empty wait proves nothing)" "$rc" "1"

chart noversion ""
printf 'noversion https://n.non.jdwlabs.com/actuator/info\n' > "$WORK_DIR/t-nov"
out=$(CHARTS_DIR="$CHARTS" bash "$SCRIPT" "$WORK_DIR/t-nov" non 2>&1); rc=$?
assert_eq "unresolvable version: exits one" "$rc" "1"

out=$(bash "$SCRIPT" "$targets" 2>&1); rc=$?
assert_eq "wrong argument count: usage error" "$rc" "2"
out=$(bash "$SCRIPT" "$targets" 'no/n' 2>&1); rc=$?
assert_eq "invalid env: usage error" "$rc" "2"

# The real configuration must always resolve, one line per target.
out=$(cd "$ROOT" && bash "$SCRIPT" .github/e2e-version-targets non); rc=$?
assert_eq "repository targets: resolve" "$rc" "0"
assert_eq "repository targets: one line per configured target" \
  "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" \
  "$(grep -cv '^[[:space:]]*\(#\|$\)' "$ROOT/.github/e2e-version-targets")"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
