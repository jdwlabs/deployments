#!/usr/bin/env bash
# Tests for tools/resolve-image-digest.sh.
#
# Offline tests cover reference parsing and every input-validation refusal.
# Network tests additionally prove the property that motivates the script: the
# digest it prints is the index's own digest and never one of the index's
# per-architecture children. Set RESOLVE_DIGEST_TESTS_OFFLINE=1 to skip them.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOLVER="${SCRIPT_DIR}/resolve-image-digest.sh"

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

# ---------------------------------------------------------------- parsing ----
# shellcheck source-path=SCRIPTDIR/..
# shellcheck source=resolve-image-digest.sh
source "$RESOLVER"

split_ref jdwlabs/authui
assert_eq "docker hub org image keeps its path" "${REGISTRY}|${REPO_PATH}|${API_HOST}" \
  "docker.io|jdwlabs/authui|registry-1.docker.io"

split_ref alpine
assert_eq "bare docker hub name gets the library namespace" "${REGISTRY}|${REPO_PATH}|${API_HOST}" \
  "docker.io|library/alpine|registry-1.docker.io"

split_ref ghcr.io/jdwlabs/thing
assert_eq "host-qualified reference keeps its registry" "${REGISTRY}|${REPO_PATH}|${API_HOST}" \
  "ghcr.io|jdwlabs/thing|ghcr.io"

split_ref localhost:5000/thing
assert_eq "localhost with a port is a registry, not a namespace" "${REGISTRY}|${REPO_PATH}|${API_HOST}" \
  "localhost:5000|thing|localhost:5000"

split_ref index.docker.io/jdwlabs/authui
assert_eq "index.docker.io normalises to the registry api host" "${REGISTRY}|${REPO_PATH}|${API_HOST}" \
  "index.docker.io|jdwlabs/authui|registry-1.docker.io"

# ------------------------------------------------------------- refusals -----
out=$(bash "$RESOLVER" jdwlabs/authui 2>&1); rc=$?
assert_eq "no tag argument exits non-zero" "$rc" "1"
assert_contains "no tag argument explains the usage" "$out" "usage:"

out=$(bash "$RESOLVER" jdwlabs/authui "2.0.4@sha256:$(printf '0%.0s' {1..64})" 2>&1); rc=$?
assert_eq "an already-pinned reference is refused" "$rc" "1"
assert_contains "refusal says why re-resolving a pin is wrong" "$out" "already a digest-pinned reference"

out=$(bash "$RESOLVER" jdwlabs/authui 'evil;rm -rf /' 2>&1); rc=$?
assert_eq "a tag outside the tag grammar is refused" "$rc" "1"
assert_contains "invalid tag names the offending value" "$out" "not a valid image tag"

out=$(bash "$RESOLVER" jdwlabs/authui "" 2>&1); rc=$?
assert_eq "an empty tag is refused" "$rc" "1"

if [ "${RESOLVE_DIGEST_TESTS_OFFLINE:-0}" = "1" ]; then
  printf '\nskipping network tests (RESOLVE_DIGEST_TESTS_OFFLINE=1)\n'
  printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
  [ "$FAIL" -eq 0 ] || exit 1
  exit 0
fi

# -------------------------------------------------------------- network -----
DIGEST_RE='^sha256:[0-9a-f]{64}$'
ACCEPT_ALL='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'

# Re-fetches a resolved reference by digest and returns its raw manifest, so the
# assertions below inspect the exact document the digest addresses.
manifest_by_digest() {
  local repo="$1" digest="$2" token
  split_ref "$repo"
  token=$(curl -sS --max-time 30 \
    "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${REPO_PATH}:pull" |
    sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  curl -sS --max-time 30 -H "Accept: ${ACCEPT_ALL}" -H "Authorization: Bearer ${token}" \
    "https://registry-1.docker.io/v2/${REPO_PATH}/manifests/${digest}"
}

check_multiarch_reference() {
  local label="$1" repo="$2" tag="$3"
  local digest manifest media children

  digest=$(bash "$RESOLVER" "$repo" "$tag" 2>/dev/null)
  if ! [[ "$digest" =~ $DIGEST_RE ]]; then
    no "${label}: resolves to a sha256 digest" "got '${digest}'"
    return
  fi
  ok "${label}: resolves to a sha256 digest"

  manifest=$(manifest_by_digest "$repo" "$digest")
  media=$(printf '%s' "$manifest" | jq -r '.mediaType // ""')
  case "$media" in
    application/vnd.oci.image.index.v1+json|application/vnd.docker.distribution.manifest.list.v2+json)
      ok "${label}: the digest addresses a manifest index" ;;
    *)
      no "${label}: the digest addresses a manifest index" "mediaType was '${media}'"
      return ;;
  esac

  # The property under test: the printed digest is the index itself, not any of
  # the per-architecture manifests the index lists.
  children=$(printf '%s' "$manifest" | jq -r '.manifests[].digest')
  if [ -z "$children" ]; then
    no "${label}: index lists child manifests" "no manifests[] entries returned"
    return
  fi
  if printf '%s\n' "$children" | grep -qx "$digest"; then
    no "${label}: digest is NOT a per-architecture child" "resolved digest appears in manifests[]"
  else
    ok "${label}: digest is NOT a per-architecture child"
  fi

  # Same reference resolved twice must not move; a tag that is republished
  # between the two calls would show up here rather than silently in prd.
  if [ "$(bash "$RESOLVER" "$repo" "$tag" 2>/dev/null)" = "$digest" ]; then
    ok "${label}: resolution is stable across calls"
  else
    no "${label}: resolution is stable across calls" "second call returned a different digest"
  fi
}

if command -v jq >/dev/null 2>&1; then
  check_multiarch_reference "alpine:3.20" alpine 3.20
  check_multiarch_reference "jdwlabs/container:2.0.6" jdwlabs/container 2.0.6
else
  printf 'skipping index-vs-child assertions (jq not installed)\n'
fi

out=$(bash "$RESOLVER" jdwlabs/container 0.0.0-does-not-exist 2>&1); rc=$?
assert_eq "a tag that does not exist fails loudly" "$rc" "1"
assert_contains "missing tag error is actionable" "$out" "does not exist"

out=$(bash "$RESOLVER" jdwlabs/no-such-repository-here 1.0.0 2>&1); rc=$?
assert_eq "an unknown repository fails loudly" "$rc" "1"

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
