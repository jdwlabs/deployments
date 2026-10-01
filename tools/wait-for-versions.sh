#!/usr/bin/env bash
# Polls each target's /actuator/info until it reports the expected version,
# and fails once the deadline passes.
#
# E2E fires as soon as the apps pipeline has merged its chart bumps, which is
# before ArgoCD has synced them. Testing straight away would grade the
# previous pods, so a pass or a fail would be about the wrong build.
#
# Usage: bash tools/wait-for-versions.sh <expected-file>
#   <expected-file> holds "<service> <info-url> <version>" lines, as printed by
#   tools/e2e-expected-versions.sh.
# Env:
#   WAIT_TIMEOUT_SECONDS   default 900
#   WAIT_INTERVAL_SECONDS  default 15
#   FETCH_TIMEOUT_SECONDS  default 10; caps a single fetch, so the total wait
#                          stays within WAIT_TIMEOUT_SECONDS plus this.
#   VERSION_FETCH          executable called as `$VERSION_FETCH <url>` that
#                          prints the reported version; defaults to curl + jq.
# Exit codes: 0 = every target current; 1 = deadline passed, or no targets;
# 2 = usage error or malformed targets.
set -uo pipefail

fetch_version() {
  curl -fsS --max-time "$FETCH_TIMEOUT_SECONDS" "$1" | jq -er '.build.version | strings'
}

fetch() {
  if [ -n "${VERSION_FETCH:-}" ]; then
    timeout "$FETCH_TIMEOUT_SECONDS" "$VERSION_FETCH" "$1"
  else
    fetch_version "$1"
  fi
}

main() {
  [ "$#" -eq 1 ] || { echo "usage: $(basename "$0") <expected-file>" >&2; return 2; }
  local file="$1"
  local timeout="${WAIT_TIMEOUT_SECONDS:-900}" interval="${WAIT_INTERVAL_SECONDS:-15}"
  FETCH_TIMEOUT_SECONDS="${FETCH_TIMEOUT_SECONDS:-10}"
  if [[ ! "$timeout" =~ ^[0-9]+$ || ! "$interval" =~ ^[1-9][0-9]*$ || ! "$FETCH_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
    echo "::error::WAIT_TIMEOUT_SECONDS, WAIT_INTERVAL_SECONDS and FETCH_TIMEOUT_SECONDS must be whole seconds (interval and fetch timeout at least 1)." >&2
    return 2
  fi
  [ -f "$file" ] || { echo "::error::${file} not found." >&2; return 1; }

  local -a services=() urls=() expected=() last=()
  local s u v n=0
  while read -r s u v || [ -n "$s" ]; do
    n=$((n + 1))
    [ -n "$s$u$v" ] || continue
    if [ -z "$s" ] || [ -z "$u" ] || [ -z "$v" ]; then
      echo "::error::${file} line ${n} needs '<service> <info-url> <version>'; an empty version would match a failed fetch." >&2
      return 2
    fi
    services+=("$s"); urls+=("$u"); expected+=("$v"); last+=("")
  done < "$file"
  if [ "${#services[@]}" -eq 0 ]; then
    echo "::error::${file} lists no targets; a wait on nothing proves nothing." >&2
    return 1
  fi

  local deadline i lagging now remaining first=1
  deadline=$(( $(date +%s) + timeout ))
  while :; do
    lagging=0
    for i in "${!services[@]}"; do
      # Once out of time, keep the earlier reply instead of starting another
      # fetch that could run past the bound.
      if [ "$first" -eq 1 ] || [ "$(date +%s)" -lt "$deadline" ]; then
        last[i]=$(fetch "${urls[i]}" 2>/dev/null) || last[i]=""
      fi
      [ "${last[i]}" = "${expected[i]}" ] || lagging=$((lagging + 1))
    done
    if [ "$lagging" -eq 0 ]; then
      for i in "${!services[@]}"; do
        echo "${services[i]} serves ${expected[i]}"
      done
      return 0
    fi
    first=0
    now=$(date +%s)
    if [ "$now" -ge "$deadline" ]; then
      echo "::error::timed out after ${timeout}s waiting for the expected versions:" >&2
      for i in "${!services[@]}"; do
        [ "${last[i]}" = "${expected[i]}" ] && continue
        echo "  ${services[i]}: expected ${expected[i]}, last saw ${last[i]:-nothing}" >&2
      done
      return 1
    fi
    remaining=$((deadline - now))
    sleep "$(( interval < remaining ? interval : remaining ))"
  done
}

main "$@"
