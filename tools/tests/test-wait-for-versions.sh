#!/usr/bin/env bash
# Tests for tools/wait-for-versions.sh, with a fake fetcher standing in for
# the network.
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/wait-for-versions.sh"

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
export FAKE_DIR="$WORK_DIR/fake"

# Answers from $FAKE_DIR/<url with non-alphanumerics as _>: one reply per poll,
# the last one repeating. DOWN means unreachable.
FAKE="$WORK_DIR/fake-fetch"
cat > "$FAKE" <<'EOF'
#!/usr/bin/env bash
f="$FAKE_DIR/$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_')"
[ -s "$f" ] || exit 7
reply=$(head -1 "$f")
[ "$(wc -l < "$f")" -gt 1 ] && sed -i '1d' "$f"
[ "$reply" = "DOWN" ] && exit 7
printf '%s\n' "$reply"
EOF
chmod +x "$FAKE"

answers() {
  local url="$1"; shift
  mkdir -p "$FAKE_DIR"
  printf '%s\n' "$@" > "$FAKE_DIR/$(printf '%s' "$url" | tr -c 'A-Za-z0-9' '_')"
}

A=https://a.non.jdwlabs.com/actuator/info
B=https://b.non.jdwlabs.com/actuator/info
expected="$WORK_DIR/expected"
printf 'svc-a %s 1.0.0\nsvc-b %s 2.0.0\n' "$A" "$B" > "$expected"

run() { VERSION_FETCH="$FAKE" WAIT_INTERVAL_SECONDS=1 WAIT_TIMEOUT_SECONDS="$1" bash "$SCRIPT" "$expected" 2>&1; }

rm -rf "$FAKE_DIR"; answers "$A" 1.0.0; answers "$B" 2.0.0
out=$(run 5); rc=$?
assert_eq "already rolled out: exits zero" "$rc" "0"
assert_contains "already rolled out: reports each service" "$out" "svc-b serves 2.0.0"

rm -rf "$FAKE_DIR"; answers "$A" 1.0.0; answers "$B" 1.9.9 DOWN 2.0.0
out=$(run 10); rc=$?
assert_eq "rolls out during the wait: exits zero" "$rc" "0"

rm -rf "$FAKE_DIR"; answers "$A" 1.0.0; answers "$B" 1.9.9
start=$(date +%s)
out=$(run 2); rc=$?
elapsed=$(( $(date +%s) - start ))
assert_eq "never rolls out: exits one" "$rc" "1"
assert_contains "never rolls out: names the lagging service, expectation and last reply" "$out" "svc-b: expected 2.0.0, last saw 1.9.9"
case "$out" in
  *"svc-a: expected"*) no "never rolls out: does not blame a service that is current" "$out" ;;
  *) ok "never rolls out: does not blame a service that is current" ;;
esac
if [ "$elapsed" -le 6 ]; then ok "never rolls out: stops at the deadline"; else no "never rolls out: stops at the deadline" "took ${elapsed}s"; fi

rm -rf "$FAKE_DIR"; answers "$A" 1.0.0; answers "$B" DOWN
out=$(run 2); rc=$?
assert_eq "unreachable: exits one" "$rc" "1"
assert_contains "unreachable: says nothing was seen" "$out" "svc-b: expected 2.0.0, last saw nothing"

: > "$WORK_DIR/empty"
out=$(VERSION_FETCH="$FAKE" bash "$SCRIPT" "$WORK_DIR/empty" 2>&1); rc=$?
assert_eq "no targets: exits one" "$rc" "1"

out=$(WAIT_TIMEOUT_SECONDS=soon bash "$SCRIPT" "$expected" 2>&1); rc=$?
assert_eq "non-numeric timeout: usage error" "$rc" "2"
out=$(WAIT_INTERVAL_SECONDS=0 bash "$SCRIPT" "$expected" 2>&1); rc=$?
assert_eq "zero interval: usage error" "$rc" "2"
out=$(bash "$SCRIPT" 2>&1); rc=$?
assert_eq "no argument: usage error" "$rc" "2"

HANG="$WORK_DIR/hang-fetch"
printf '#!/usr/bin/env bash\nsleep 30\n' > "$HANG"; chmod +x "$HANG"
start=$(date +%s)
out=$(VERSION_FETCH="$HANG" FETCH_TIMEOUT_SECONDS=1 WAIT_INTERVAL_SECONDS=1 WAIT_TIMEOUT_SECONDS=3 bash "$SCRIPT" "$expected" 2>&1); rc=$?
elapsed=$(( $(date +%s) - start ))
assert_eq "hung fetcher: exits one" "$rc" "1"
if [ "$elapsed" -le 5 ]; then ok "hung fetcher: total wait stays within timeout plus one fetch cap"; else no "hung fetcher: total wait stays within timeout plus one fetch cap" "took ${elapsed}s"; fi

rm -rf "$FAKE_DIR"; answers "$A" 1.0.0; answers "$B" 1.9.9
start=$(date +%s)
out=$(VERSION_FETCH="$FAKE" WAIT_INTERVAL_SECONDS=30 WAIT_TIMEOUT_SECONDS=2 bash "$SCRIPT" "$expected" 2>&1); rc=$?
elapsed=$(( $(date +%s) - start ))
if [ "$elapsed" -le 4 ]; then ok "long interval: sleep is clipped to the deadline"; else no "long interval: sleep is clipped to the deadline" "took ${elapsed}s"; fi

printf 'svc-a %s 1.0.0\nsvc-b %s 2.0.0' "$A" "$B" > "$WORK_DIR/nonl"
out=$(VERSION_FETCH="$FAKE" WAIT_TIMEOUT_SECONDS=1 WAIT_INTERVAL_SECONDS=1 bash "$SCRIPT" "$WORK_DIR/nonl" 2>&1); rc=$?
assert_eq "no trailing newline: the last target is still waited on" "$rc" "1"
assert_contains "no trailing newline: the lagging last target is named" "$out" "svc-b: expected 2.0.0"

for bad in 'svc-a https://a.non.jdwlabs.com/actuator/info' 'svc-a'; do
  printf '%s\n' "$bad" > "$WORK_DIR/bad"
  out=$(VERSION_FETCH="$FAKE" WAIT_TIMEOUT_SECONDS=2 WAIT_INTERVAL_SECONDS=1 bash "$SCRIPT" "$WORK_DIR/bad" 2>&1); rc=$?
  assert_eq "malformed line '$bad': usage error" "$rc" "2"
  assert_contains "malformed line '$bad': names the line" "$out" "line 1"
done

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
