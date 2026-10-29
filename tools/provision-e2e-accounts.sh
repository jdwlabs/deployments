#!/usr/bin/env bash
# One-time: create the two seeded E2E accounts on `non` and store their
# credentials as the `non` environment secrets of jdwlabs/deployments.
# Run it in your own terminal, not through an agent. It never prints a password.
set -euo pipefail
# An inherited `bash -x` or SHELLOPTS=xtrace would echo every expanded password.
set +x

BASE=https://usersrole.non.jdwlabs.com
REPO=jdwlabs/deployments
USER_EMAIL=e2e-seeded-user@example.com
ADMIN_EMAIL=e2e-seeded-admin@example.com
DB_NS=database
DB_CLUSTER=platform-postgresql-cluster-non
DB_NAME=jdwlabs_non
ADMIN_ROLE_ID=1

for tool in gh kubectl curl tr head; do
  command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done

# 12 random alphanumerics plus a fixed tail so the policy (upper, lower, digit,
# symbol, >= 8) always holds.
gen() { printf '%s%s' "$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 12)" 'Aa1!'; }
USER_PW=$(gen)
ADMIN_PW=$(gen)

body() { printf '{"emailAddress":"%s","password":"%s"}' "$1" "$2"; }

post() { # path email password -> http status
  body "$2" "$3" | curl -s -o /dev/null -w '%{http_code}' \
    -H 'Content-Type: application/json' --data-binary @- "$BASE$1"
}

# A failover moves the primary, and the grants below need a writable instance.
DB_POD=$(kubectl -n "$DB_NS" get cluster.postgresql.cnpg.io "$DB_CLUSTER" \
  -o jsonpath='{.status.currentPrimary}')
[ -n "$DB_POD" ] || { echo "could not resolve the $DB_CLUSTER primary" >&2; exit 1; }

# Fail before creating anything if the secrets could not be written afterwards.
gh secret list --env non -R "$REPO" >/dev/null \
  || { echo "cannot list secrets on the non environment" >&2; exit 1; }

echo "Checking $BASE is reachable..."
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/actuator/info")" = 200 ] \
  || { echo "non API not reachable" >&2; exit 1; }

# Each password is stored the moment its account exists, so a later failure never
# leaves a registered account whose password only lived in this process.
CREATED=()
report_partial() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "${#CREATED[@]}" -gt 0 ]; then
    echo "Failed after registering: ${CREATED[*]}. Their credentials are already in the non environment secrets; delete these accounts before re-running." >&2
  fi
}
trap report_partial EXIT

echo "Registering the accounts and storing their secrets..."
register() { # email password secret-prefix
  code=$(post /auth/user "$1" "$2")
  if [ "$code" != 201 ]; then
    echo "registering $1 returned HTTP $code (409 means it already exists)." >&2
    echo "Delete the existing account or pick another address, then re-run." >&2
    exit 1
  fi
  CREATED+=("$1")
  printf %s "$1" | gh secret set "E2E_$3_EMAIL"    --env non -R "$REPO"
  printf %s "$2" | gh secret set "E2E_$3_PASSWORD" --env non -R "$REPO"
}
register "$USER_EMAIL" "$USER_PW" USER
register "$ADMIN_EMAIL" "$ADMIN_PW" ADMIN

echo "Granting the admin role to $ADMIN_EMAIL (one auth.users_roles row)..."
kubectl -n "$DB_NS" exec -i "$DB_POD" -c postgres -- \
  psql -U postgres -d "$DB_NAME" -v ON_ERROR_STOP=1 -v email="$ADMIN_EMAIL" -v role="$ADMIN_ROLE_ID" <<'SQL'
INSERT INTO auth.users_roles (user_id, role_id, created_by_user_id, created_time)
SELECT user_id, :role, user_id, now() FROM auth.users WHERE email_address = :'email'
ON CONFLICT (user_id, role_id) DO NOTHING;
SQL

echo "Creating the seeded user's profile (the e2e flow reads it and 404s without one)..."
kubectl -n "$DB_NS" exec -i "$DB_POD" -c postgres -- \
  psql -U postgres -d "$DB_NAME" -v ON_ERROR_STOP=1 -v email="$USER_EMAIL" <<'SQL'
INSERT INTO auth.profiles (user_id, first_name, last_name, birthdate, created_by_user_id, created_time, modified_by_user_id, modified_time)
SELECT user_id, 'E2E', 'User', DATE '2000-01-01', user_id, now(), user_id, now()
FROM auth.users u WHERE email_address = :'email'
  AND NOT EXISTS (SELECT 1 FROM auth.profiles p WHERE p.user_id = u.user_id);
SQL

echo "Verifying both can log in..."
for pair in "$USER_EMAIL:$USER_PW" "$ADMIN_EMAIL:$ADMIN_PW"; do
  [ "$(post /auth/authenticate "${pair%%:*}" "${pair#*:}")" = 200 ] \
    || { echo "login failed for ${pair%%:*}" >&2; exit 1; }
done

# Provisioning is complete; a listing failure below must not tell the operator
# to delete working accounts.
CREATED=()
echo "Done. Secrets now on the non environment:"
gh secret list --env non -R "$REPO" || echo "could not list secrets; provisioning itself succeeded" >&2
