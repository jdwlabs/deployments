#!/usr/bin/env bash
# Fails when any named environment variable is unset or empty.
#
# The E2E gate reads its seeded accounts from the `non` GitHub environment. A
# secret that was never created expands to an empty string rather than an
# error, and the harness would then fail test by test with a login message
# instead of a configuration one. Checking first names every missing secret at
# once and stops before any request reaches non.
#
# Usage: bash tools/require-env.sh NAME...
# Exit codes: 0 = all set; 1 = at least one missing; 2 = usage error.
set -uo pipefail

main() {
  { set +x; } 2>/dev/null
  [ "$#" -ge 1 ] || { echo "usage: $(basename "$0") NAME..." >&2; return 2; }
  local missing=0 name
  for name in "$@"; do
    if [[ ! "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "::error::'${name}' is not an environment variable name." >&2
      return 2
    fi
    if [ -z "${!name:-}" ]; then
      echo "::error::${name} is not set. Add it as a secret on the GitHub environment this job uses." >&2
      missing=$((missing + 1))
    fi
  done
  [ "$missing" -eq 0 ]
}

main "$@"
