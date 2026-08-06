#!/usr/bin/env bash
# Parses .github/prd-auto-promote into the list of chart names enabled for
# automatic prd promotion.
#
# Isolated from .github/workflows/promote-prd.yml so the CRLF-handling and
# chart-name validation it does can be unit tested directly rather than only
# through the workflow's own dormant auto-promotion trigger.
#
# `\r` is stripped before comment/blank-line stripping and before validation,
# unconditionally -- not just because the allowlist file is pinned to LF via
# .gitattributes, but because a parser that only works when the file happens
# to be LF is the same landmine with a different trigger. Without this, a
# CRLF-terminated entry fails ^[a-z0-9-]+$ silently and reads as "nothing to
# promote".
#
# Usage:
#   tools/parse-prd-auto-promote.sh <allowlist-file>
#
# Prints one valid chart name per line to stdout. An entry that survives
# comment/blank stripping but fails the chart-name grammar is a refusal, not
# a skip: a malformed name signals something is wrong with the allowlist file
# itself, so every bad entry is reported via ::error:: on stderr and the
# script exits non-zero -- the caller decides whether that fails the job (the
# Promote PRD workflow does).
#
# Exit codes: 0 = every entry (if any) was a valid chart name; 1 = at least
# one entry failed validation, or the wrong number of arguments was given.
set -uo pipefail

valid_app() {
  [[ "$1" =~ ^[a-z0-9-]+$ ]]
}

parse_allowlist() {
  local file="$1" invalid=0 line

  if [ ! -f "$file" ]; then
    echo "No charts are enabled for automatic prd promotion (${file} not found). Nothing to do." >&2
    return 0
  fi

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    if valid_app "$line"; then
      printf '%s\n' "$line"
    else
      echo "::error::allowlisted entry '${line}' in ${file} is not a valid chart name (expected ^[a-z0-9-]+\$)." >&2
      invalid=$((invalid + 1))
    fi
  done < <(tr -d '\r' < "$file" | grep -v '^[[:space:]]*#' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d')

  [ "$invalid" -eq 0 ]
}

main() {
  [ "$#" -eq 1 ] || { echo "usage: $(basename "$0") <allowlist-file>" >&2; exit 1; }
  parse_allowlist "$1"
}

# Sourcing the script exposes the functions for tests without running main.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
