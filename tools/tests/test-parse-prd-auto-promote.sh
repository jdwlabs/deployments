#!/usr/bin/env bash
# Tests for tools/parse-prd-auto-promote.sh.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARSER="${SCRIPT_DIR}/parse-prd-auto-promote.sh"

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

# ------------------------------------------------------------------ CRLF ----
# The exact fault the ticket describes: a chart name whose line ends \r\n.
crlf_file="$WORK_DIR/crlf-allowlist"
printf '# a comment\r\nrolesui\r\nauthui\r\n' > "$crlf_file"

out=$(bash "$PARSER" "$crlf_file"); rc=$?
assert_eq "CRLF file: exits zero" "$rc" "0"
assert_eq "CRLF file: strips \\r and yields clean chart names" "$out" "$(printf 'rolesui\nauthui')"

# A CRLF-terminated entry must never reach the validation regex still
# carrying its \r -- that is exactly how this bug hid: ^[a-z0-9-]+$ against
# "rolesui\r" fails, and the failure used to be a silent ::warning::.
case "$out" in
  *$'\r'*) no "CRLF file: no entry keeps a trailing \\r" "output contained a literal CR" ;;
  *) ok "CRLF file: no entry keeps a trailing \\r" ;;
esac

# --------------------------------------------------------------- LF/mixed ---
lf_file="$WORK_DIR/lf-allowlist"
printf '# comment\nrolesui\n\n  authui  \n' > "$lf_file"
out=$(bash "$PARSER" "$lf_file"); rc=$?
assert_eq "LF file: exits zero" "$rc" "0"
assert_eq "LF file: comments and blanks dropped, surrounding whitespace trimmed" "$out" "$(printf 'rolesui\nauthui')"

# ------------------------------------------------------------- empty file ---
empty_file="$WORK_DIR/empty-allowlist"
: > "$empty_file"
out=$(bash "$PARSER" "$empty_file"); rc=$?
assert_eq "empty file: exits zero" "$rc" "0"
assert_eq "empty file: prints nothing" "$out" ""

# -------------------------------------------------------- comment-only -----
comment_file="$WORK_DIR/comment-only-allowlist"
printf '# nothing enabled yet\r\n# still nothing\r\n' > "$comment_file"
out=$(bash "$PARSER" "$comment_file"); rc=$?
assert_eq "comment-only CRLF file: exits zero" "$rc" "0"
assert_eq "comment-only CRLF file: prints nothing" "$out" ""

# ------------------------------------------------------------ missing file --
out=$(bash "$PARSER" "$WORK_DIR/does-not-exist"); rc=$?
assert_eq "missing allowlist file: exits zero (nothing to promote)" "$rc" "0"
assert_eq "missing allowlist file: prints nothing" "$out" ""

# --------------------------------------------------------- invalid entry ---
# A malformed chart name must fail the run loudly, never a silent
# ::warning:: that leaves it looking like there was nothing to promote.
invalid_file="$WORK_DIR/invalid-allowlist"
printf 'rolesui\r\nNot_Valid\r\n' > "$invalid_file"
out=$(bash "$PARSER" "$invalid_file" 2>&1 >/dev/null); rc=$?
stdout_out=$(bash "$PARSER" "$invalid_file" 2>/dev/null)
assert_eq "invalid entry: exits non-zero" "$rc" "1"
assert_contains "invalid entry: emits ::error::, not ::warning::" "$out" "::error::"
assert_contains "invalid entry: names the offending value" "$out" "Not_Valid"
assert_eq "invalid entry: still prints the valid chart names it found" "$stdout_out" "rolesui"

# --------------------------------------------------------------- usage -----
out=$(bash "$PARSER" 2>&1); rc=$?
assert_eq "no arguments exits non-zero" "$rc" "1"
assert_contains "no arguments explains usage" "$out" "usage:"

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
