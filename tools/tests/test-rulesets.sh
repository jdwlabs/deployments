#!/usr/bin/env bash
# Tests for the E2E test-branch ruleset and the naming ruleset it must agree
# with. Neither ruleset is exercised by a pull request, so a typo would surface
# only when a human applies them, or when a legitimate branch is refused.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIR="${ROOT}/.github/rulesets"
E2E="${DIR}/e2e-test-branches.json"
NAMING="${DIR}/branch-naming-convention.json"

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf 'FAIL %s\n     %s\n' "$1" "$2"; }

assert_eq() {
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "expected '$3', got '$2'"; fi
}

for f in "$DIR"/*.json; do
  if jq empty "$f" 2>/dev/null; then ok "$(basename "$f") is valid JSON"; else no "$(basename "$f") is valid JSON" "jq could not parse it"; fi
done

assert_eq "e2e ruleset: name" "$(jq -r '.name' "$E2E" 2>/dev/null)" "E2E Test Branches"
assert_eq "e2e ruleset: targets branches" "$(jq -r '.target' "$E2E" 2>/dev/null)" "branch"
assert_eq "e2e ruleset: enforced, not evaluate-only" "$(jq -r '.enforcement' "$E2E" 2>/dev/null)" "active"
assert_eq "e2e ruleset: covers every depth under e2e-test/ and nothing else" \
  "$(jq -c '.conditions.ref_name' "$E2E" 2>/dev/null)" '{"exclude":[],"include":["refs/heads/e2e-test/**"]}'
assert_eq "e2e ruleset: restricts creation, update and deletion" \
  "$(jq -c '[.rules[].type] | sort' "$E2E" 2>/dev/null)" '["creation","deletion","update"]'
# The bot is an Integration; it must never be listed, or it could push here.
assert_eq "e2e ruleset: only organization admins bypass" \
  "$(jq -c '.bypass_actors' "$E2E" 2>/dev/null)" '[{"actor_id":null,"actor_type":"OrganizationAdmin","bypass_mode":"always"}]'

pattern=$(jq -r '.rules[] | select(.type == "branch_name_pattern") | .parameters.pattern' "$NAMING" 2>/dev/null)
for good in e2e-test/JDWLABS-671 feat/JDWLABS-671-x chore/x; do
  if printf '%s' "$good" | grep -Eq "$pattern"; then ok "naming: '${good}' is allowed"; else no "naming: '${good}' is allowed" "pattern: $pattern"; fi
done
for bad in e2e-tests/x wip/x e2e-test; do
  if printf '%s' "$bad" | grep -Eq "$pattern"; then no "naming: '${bad}' is still refused" "pattern: $pattern"; else ok "naming: '${bad}' is still refused"; fi
done

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
