#!/usr/bin/env bash
# Structural checks on the E2E gate and its link to Promote PRD. Neither
# workflow runs on a pull request, so a rename, a dropped trigger or a leaked
# artifact would otherwise surface only after merge, as promotion quietly never
# firing or a password in a public download.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E="${ROOT}/.github/workflows/e2e.yml"
PROMOTE="${ROOT}/.github/workflows/promote-prd.yml"

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf 'FAIL %s\n     %s\n' "$1" "$2"; }

assert_eq() {
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "expected '$3', got '$2'"; fi
}

assert_before() {
  if [[ "$2" =~ ^[0-9]+$ && "$3" =~ ^[0-9]+$ ]] && [ "$2" -lt "$3" ]; then
    ok "$1"
  else
    no "$1" "step indexes '$2' and '$3'"
  fi
}

step_index() {
  yq ".jobs[\"api-gate\"].steps | to_entries | map(select((.value.run // \"\") | test(\"$1\"))) | .[0].key" "$E2E"
}

assert_eq "the gate workflow is named exactly E2E" "$(yq '.name' "$E2E")" "E2E"
assert_eq "Promote PRD follows exactly the E2E workflow" \
  "$(yq -o=json -I=0 '.on.workflow_run.workflows' "$PROMOTE")" '["E2E"]'
assert_eq "Promote PRD follows only E2E runs on main" \
  "$(yq -o=json -I=0 '.on.workflow_run.branches' "$PROMOTE")" '["main"]'
assert_eq "E2E receives apps-deployed" \
  "$(yq -o=json -I=0 '.on.repository_dispatch.types' "$E2E")" '["apps-deployed"]'
assert_eq "E2E can be dispatched by hand" "$(yq '.on | has("workflow_dispatch")' "$E2E")" "true"
assert_eq "E2E has no trigger other than dispatches" \
  "$(yq -o=json -I=0 '.on | keys' "$E2E")" '["repository_dispatch","workflow_dispatch"]'
assert_eq "the canary input offers exactly none, version, credentials" \
  "$(yq -o=json -I=0 '.on.workflow_dispatch.inputs.canary.options' "$E2E")" '["none","version","credentials"]'
assert_eq "the gate runs on a GitHub-hosted runner" "$(yq '.jobs["api-gate"].runs-on' "$E2E")" "ubuntu-latest"
assert_eq "the gate reads the non environment" "$(yq '.jobs["api-gate"].environment' "$E2E")" "non"
assert_eq "workflow-level permissions are empty" "$(yq -o=json -I=0 '.permissions' "$E2E")" '{}'

image=$(yq '.jobs["api-gate"].env.E2E_IMAGE' "$E2E")
if [[ "$image" =~ ^ghcr\.io/jdwlabs/platform-e2e-api:[0-9A-Za-z._-]+@sha256:[0-9a-f]{64}$ ]]; then
  ok "the gate image is pinned by digest"
else
  no "the gate image is pinned by digest" "got '$image'"
fi

unpinned=$(yq '.jobs[].steps[].uses | select(. != null)' "$E2E" | grep -Ev '@[0-9a-f]{40}$' || true)
assert_eq "every action is pinned to a commit SHA" "$unpinned" ""

arc=$(grep -rl 'ubuntu-jdwlabs' "${ROOT}/.github" || true)
assert_eq "nothing under .github targets the retired self-hosted runner" "$arc" ""

guard=$(step_index 'require-env.sh')
wait=$(step_index 'wait-for-versions.sh')
gate=$(step_index 'docker run')
assert_before "the secret guard runs before the version wait" "$guard" "$wait"
assert_before "the version wait runs before the gate" "$wait" "$gate"

guard_run=$(yq ".jobs[\"api-gate\"].steps[${guard:-0}].run" "$E2E")
while read -r secret; do
  case "$guard_run" in
    *"$secret"*) ok "the guard checks ${secret}" ;;
    *) no "the guard checks ${secret}" "guard step: $guard_run" ;;
  esac
done < <(grep -o 'secrets\.E2E_[A-Z_]*' "$E2E" | sed 's/^secrets\.//' | sort -u)

paths=$(yq '.jobs["api-gate"].steps[] | select((.uses // "") | test("upload-artifact")) | .with.path' "$E2E")
# shellcheck disable=SC2016 # the expression is matched literally
assert_eq "the report artifact is exactly junit.xml" "$paths" '${{ runner.temp }}/e2e-report/junit.xml'

scan=$(yq '.jobs["api-gate"].steps | to_entries | map(select(.value.name == "Refuse to publish a report that carries credentials")) | .[0].key' "$E2E")
upload=$(yq '.jobs["api-gate"].steps | to_entries | map(select((.value.uses // "") | test("upload-artifact"))) | .[0].key' "$E2E")
assert_before "the credential scan runs before the upload" "$scan" "$upload"
upload_if=$(yq '.jobs["api-gate"].steps[] | select((.uses // "") | test("upload-artifact")) | .if' "$E2E")
case "$upload_if" in
  *"steps.scan.outcome == 'success'"*) ok "the upload runs only when the scan succeeded" ;;
  *) no "the upload runs only when the scan succeeded" "if: $upload_if" ;;
esac
assert_eq "the scan step is the one the upload gates on" \
  "$(yq '.jobs["api-gate"].steps[] | select(.name == "Refuse to publish a report that carries credentials") | .id' "$E2E")" "scan"
assert_eq "checkout keeps no credentials in the workspace" \
  "$(yq '.jobs["api-gate"].steps[0].with["persist-credentials"]' "$E2E")" "false"

# Run the scan step's own script against fabricated reports: a structural
# match would not catch a fail-open branch or an unescaped comparison.
scan_script="$WORK_DIR/scan.sh"
yq '.jobs["api-gate"].steps[] | select(.id == "scan") | .run' "$E2E" > "$scan_script"
run_scan() {
  local body="$1"
  rm -rf "$WORK_DIR/tmp"; mkdir -p "$WORK_DIR/tmp/e2e-report"
  printf '%s' "$body" > "$WORK_DIR/tmp/e2e-report/junit.xml"
  RUNNER_TEMP="$WORK_DIR/tmp" E2E_USER_EMAIL='u@example.test' E2E_USER_PASSWORD="p&w<d>\"x'y" \
    E2E_ADMIN_EMAIL='a@example.test' E2E_ADMIN_PASSWORD='plain-admin-pw' \
    bash -e "$scan_script" > "$WORK_DIR/scan.out" 2>&1
}
report_kept() { [ -f "$WORK_DIR/tmp/e2e-report/junit.xml" ] && echo kept || echo removed; }

run_scan '<testsuite><testcase name="ok"/></testsuite>'; rc=$?
assert_eq "scan: a clean report passes" "$rc/$(report_kept)" "0/kept"
run_scan '<failure>login with plain-admin-pw failed</failure>'; rc=$?
assert_eq "scan: a literal seeded value removes the report" "$rc/$(report_kept)" "0/removed"
run_scan '<failure>login with p&amp;w&lt;d&gt;&quot;x&apos;y failed</failure>'; rc=$?
assert_eq "scan: an XML-escaped seeded value removes the report" "$rc/$(report_kept)" "0/removed"
run_scan '<system-out>Authorization: Basic abc</system-out>'; rc=$?
assert_eq "scan: an Authorization header removes the report" "$rc/$(report_kept)" "0/removed"
if [ "$(id -u)" -ne 0 ]; then
  run_scan '<testsuite/>'; chmod 000 "$WORK_DIR/tmp/e2e-report/junit.xml"
  RUNNER_TEMP="$WORK_DIR/tmp" E2E_USER_EMAIL=x E2E_USER_PASSWORD=x E2E_ADMIN_EMAIL=x E2E_ADMIN_PASSWORD=x \
    bash -e "$scan_script" > "$WORK_DIR/scan.out" 2>&1; rc=$?
  assert_eq "scan: a grep error fails the step instead of passing" "$rc" "1"
fi
if grep -q 'a&b\|p&w' "$WORK_DIR/scan.out"; then no "scan: seeded values stay out of the log" "found in output"; else ok "scan: seeded values stay out of the log"; fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
