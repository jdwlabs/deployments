#!/usr/bin/env bash
# Tests for tools/require-env.sh.
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/require-env.sh"

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

out=$(A_ONE=x A_TWO=y bash "$SCRIPT" A_ONE A_TWO 2>&1); rc=$?
assert_eq "all set: exits zero" "$rc" "0"
assert_eq "all set: says nothing" "$out" ""

# An unset secret reaches a workflow step as an empty string, not as an
# absent variable, so empty must count as missing.
out=$(A_ONE=x A_TWO="" bash "$SCRIPT" A_ONE A_TWO A_THREE 2>&1); rc=$?
assert_eq "missing: exits one" "$rc" "1"
assert_contains "missing: names an empty variable" "$out" "::error::A_TWO is not set"
assert_contains "missing: names an unset variable" "$out" "::error::A_THREE is not set"
case "$out" in
  *A_ONE*) no "missing: does not name a set variable" "$out" ;;
  *) ok "missing: does not name a set variable" ;;
esac

out=$(bash "$SCRIPT" 2>&1); rc=$?
assert_eq "no arguments: usage error" "$rc" "2"

out=$(bash "$SCRIPT" 'A;B' 2>&1); rc=$?
assert_eq "invalid name: usage error" "$rc" "2"
assert_contains "invalid name: says why" "$out" "is not an environment variable name"

# Regression tests: secrets must not leak in shell traces (set -x).
out=$(SECRET=sekrit123 bash -x "$SCRIPT" SECRET 2>&1); rc=$?
assert_eq "trace: secret success path hides value" "$rc" "0"
case "$out" in
  *sekrit123*) no "trace: secret value not leaked under bash -x" "$out" ;;
  *) ok "trace: secret value not leaked under bash -x" ;;
esac

out=$(A_ONE=value A_SECRET=hidden bash -x "$SCRIPT" A_ONE A_SECRET 2>&1); rc=$?
assert_eq "trace: success with multiple vars" "$rc" "0"
case "$out" in
  *hidden*) no "trace: multi-var secret not leaked under bash -x" "$out" ;;
  *) ok "trace: multi-var secret not leaked under bash -x" ;;
esac

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
