#!/usr/bin/env bash
# Resolve a container image tag to the digest of the manifest the tag points at,
# and print "sha256:<64 hex>" on stdout.
#
# For a multi-arch tag that digest is the OCI index / Docker manifest-list
# digest. Resolving to a per-architecture CHILD manifest digest instead is a
# recurring trap here: a pod's `.status.containerStatuses[].imageID` reports the
# child manifest its node pulled, so a child digest written into chart values
# looks like drift against the index digest every other tool reports, and
# containerd's config-digest deduplication makes the mismatch hard to unpick
# after the fact. This script never selects a platform: it sends index media
# types first in Accept, never a platform hint, and refuses to emit anything but
# the digest of the exact manifest document the registry returned for the tag.
#
# Usage:
#   tools/resolve-image-digest.sh <repository> <tag>
#
#   repository  Image repository, with or without a registry host
#               (jdwlabs/authui, ghcr.io/owner/name, alpine).
#   tag         Plain tag. A digest-pinned reference is rejected: pinning is the
#               caller's decision, and re-resolving an already pinned reference
#               would silently move it.
#
# Exit codes: 0 = digest printed; 1 = could not resolve (reason on stderr).
# A caller must treat any non-zero exit as fatal. Falling back to a bare tag
# reintroduces the mutable reference this exists to prevent.
set -uo pipefail

ATTEMPTS=3
RETRY_SLEEP=3
CURL_TIMEOUT=30

# Index types first so a multi-arch tag resolves to its index, then the single
# manifest types so a genuinely single-arch tag still resolves.
ACCEPT='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'

log() { printf 'resolve-image-digest: %s\n' "$*" >&2; }
die() {
  printf 'resolve-image-digest: ERROR: %s\n' "$*" >&2
  exit 1
}

# Splits "[host/]path" into REGISTRY / REPO_PATH / API_HOST using the same rule
# the container runtimes use: the first component is a registry host only when
# it looks like one (contains a dot or a port, or is localhost).
split_ref() {
  local repo="$1" first
  first="${repo%%/*}"
  if [[ "$repo" == */* && ( "$first" == *.* || "$first" == *:* || "$first" == "localhost" ) ]]; then
    REGISTRY="$first"
    REPO_PATH="${repo#*/}"
  else
    REGISTRY="docker.io"
    REPO_PATH="$repo"
  fi

  if [[ "$REGISTRY" == "docker.io" || "$REGISTRY" == "index.docker.io" ]]; then
    API_HOST="registry-1.docker.io"
    [[ "$REPO_PATH" == */* ]] || REPO_PATH="library/${REPO_PATH}"
  else
    API_HOST="$REGISTRY"
  fi
}

# Exchanges a WWW-Authenticate challenge for an anonymous pull token. Public
# repositories need this on Docker Hub and ghcr.io alike; a private repository
# fails here with a 401 the caller sees rather than an empty result.
bearer_token() {
  local challenge="$1" realm service url body
  realm=$(printf '%s' "$challenge" | sed -n 's/.*[Rr]ealm="\([^"]*\)".*/\1/p')
  service=$(printf '%s' "$challenge" | sed -n 's/.*service="\([^"]*\)".*/\1/p')
  [ -n "$realm" ] || return 1

  url="${realm}?scope=repository:${REPO_PATH}:pull"
  [ -n "$service" ] && url="${url}&service=${service}"

  body=$(curl -sS --max-time "$CURL_TIMEOUT" "$url") || return 1
  printf '%s' "$body" | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
}

# One resolution attempt. Writes the digest to $OUT_FILE.
# Returns 0 on success, 2 when the failure is worth retrying, and exits via die
# for anything a retry cannot change (missing tag, private repo, bad response).
resolve_once() {
  local url="https://${API_HOST}/v2/${REPO_PATH}/manifests/${TAG}"
  local code token challenge content_type digest body_digest

  code=$(curl -sS --max-time "$CURL_TIMEOUT" -H "Accept: ${ACCEPT}" \
    -D "$HDR_FILE" -o "$BODY_FILE" -w '%{http_code}' "$url" 2>"$ERR_FILE")
  if [ -z "$code" ] || [ "$code" = "000" ]; then
    log "request to ${url} failed: $(tr -d '\r' < "$ERR_FILE" | tr '\n' ' ')"
    return 2
  fi

  if [ "$code" = "401" ]; then
    challenge=$(grep -i '^www-authenticate:' "$HDR_FILE" | head -1 | tr -d '\r')
    [ -n "$challenge" ] || die "registry ${API_HOST} returned 401 for ${REPO_PATH}:${TAG} with no auth challenge to answer."
    token=$(bearer_token "$challenge")
    if [ -z "$token" ]; then
      log "could not obtain a pull token from the registry auth challenge"
      return 2
    fi
    code=$(curl -sS --max-time "$CURL_TIMEOUT" -H "Accept: ${ACCEPT}" \
      -H "Authorization: Bearer ${token}" \
      -D "$HDR_FILE" -o "$BODY_FILE" -w '%{http_code}' "$url" 2>"$ERR_FILE")
  fi

  case "$code" in
    200) ;;
    404)
      die "tag '${TAG}' does not exist in ${REGISTRY}/${REPO_PATH}. Check the tag was actually published (the release pipeline may still be running) before retrying." ;;
    401|403)
      die "registry ${API_HOST} refused anonymous pull of ${REPO_PATH}:${TAG} (HTTP ${code}). This resolver only supports public repositories; a private one needs registry credentials wired into the workflow." ;;
    429|5??)
      log "registry returned HTTP ${code} for ${REPO_PATH}:${TAG}"
      return 2 ;;
    *)
      die "unexpected HTTP ${code} from ${API_HOST} for ${REPO_PATH}:${TAG}." ;;
  esac

  content_type=$(grep -i '^content-type:' "$HDR_FILE" | tail -1 | tr -d '\r' | sed 's/^[^:]*:[[:space:]]*//;s/;.*//')
  digest=$(grep -i '^docker-content-digest:' "$HDR_FILE" | tail -1 | tr -d '\r' | sed 's/^[^:]*:[[:space:]]*//')

  # The digest of a manifest is the sha256 of its exact bytes, so the response
  # body is an independent second source. Used as the answer when the registry
  # omits the header, and as a cross-check when it does not: a mismatch means
  # the bytes are not the document the header describes, and guessing which one
  # is right is exactly the mistake this script exists to avoid.
  body_digest="sha256:$(sha256sum "$BODY_FILE" | cut -d' ' -f1)"
  if [ -z "$digest" ]; then
    digest="$body_digest"
  elif [ "$digest" != "$body_digest" ]; then
    die "registry reported ${digest} for ${REPO_PATH}:${TAG} but the returned manifest bytes hash to ${body_digest}. Refusing to guess which is the real reference."
  fi

  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] ||
    die "registry returned an unusable digest '${digest}' for ${REPO_PATH}:${TAG}."

  case "$content_type" in
    application/vnd.oci.image.index.v1+json|application/vnd.docker.distribution.manifest.list.v2+json)
      log "${REGISTRY}/${REPO_PATH}:${TAG} -> ${digest} (multi-arch index)" ;;
    application/vnd.oci.image.manifest.v1+json|application/vnd.docker.distribution.manifest.v2+json)
      # Not the child-digest trap: index media types were offered first and the
      # registry still returned a plain manifest, so no index exists above this
      # tag and this digest is the only reference it has.
      log "${REGISTRY}/${REPO_PATH}:${TAG} -> ${digest} (single-platform image, no index exists for this tag)" ;;
    *)
      die "registry returned unexpected manifest content-type '${content_type}' for ${REPO_PATH}:${TAG}; refusing to treat it as an index digest." ;;
  esac

  printf '%s' "$digest" > "$OUT_FILE"
  return 0
}

main() {
  [ "$#" -eq 2 ] || die "usage: $(basename "$0") <repository> <tag>"

  local repository="$1"
  TAG="$2"

  [ -n "$repository" ] || die "repository must not be empty."
  [ -n "$TAG" ] || die "tag must not be empty."
  case "$TAG" in
    *@*) die "'${TAG}' is already a digest-pinned reference; pass the plain tag. Re-resolving a pin would silently move it off the digest someone chose." ;;
  esac
  [[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$ ]] ||
    die "'${TAG}' is not a valid image tag."

  split_ref "$repository"

  WORK_DIR=$(mktemp -d) || die "could not create a temporary directory."
  trap 'rm -rf "$WORK_DIR"' EXIT
  HDR_FILE="$WORK_DIR/headers"
  BODY_FILE="$WORK_DIR/body"
  ERR_FILE="$WORK_DIR/err"
  OUT_FILE="$WORK_DIR/digest"

  local attempt=1 rc
  while : ; do
    resolve_once
    rc=$?
    [ "$rc" -eq 0 ] && break
    if [ "$attempt" -ge "$ATTEMPTS" ]; then
      die "could not resolve ${REGISTRY}/${REPO_PATH}:${TAG} to a digest after ${ATTEMPTS} attempts. The tag cannot be promoted without one - re-run once the registry is reachable, or dispatch the promotion with an explicit tag@sha256:<index digest>."
    fi
    log "attempt ${attempt}/${ATTEMPTS} failed; retrying in ${RETRY_SLEEP}s"
    sleep "$RETRY_SLEEP"
    attempt=$((attempt + 1))
  done

  cat "$OUT_FILE"
  printf '\n'
}

# Sourcing the script exposes the functions for tests without running main.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
