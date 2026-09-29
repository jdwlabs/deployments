# usersrole → Go services cutover

Moves live traffic on `usersrole.<env>.jdwlabs.com` from the JVM `usersrole`
to `profile-service` and `identity-service`, one path group at a time. The JVM
keeps its catch-all route and keeps running throughout, so every step is
undone by reverting one values change.

## How routing works

`usersrole` owns an HTTPRoute with a single catch-all rule for the hostname.
Each step adds a rule with a `PathPrefix` match to a Go service's own
HTTPRoute on the **same hostname**. The Gateway sends each request to the most
specific matching rule across all routes, so the Go service takes only the
prefixes it claims and every other path stays on the JVM. `PathPrefix` matches
whole path segments: `/api/profiles` matches `/api/profiles` and
`/api/profiles/7/icon`, but not `/api/profilesX`.

Frontends are untouched. All three read one `AUTH_BASE_URL`, so no client
deploy is coupled to any step.

## Preconditions

| Check | non | prd |
|---|---|---|
| Go issuer origin equals what the JVM stamps (`https://usersrole.<env>.jdwlabs.com:443`) | done (#259) | #260 |
| Tenant appset uses ServerSideDiff with no HTTPRoute ignores, so `matches` reach the cluster | done (platform #464) | done |
| App has `prune: true`, so reverting `ingress` deletes the route | yes | yes |

Why the issuer origin has `:443`: the JVM builds `iss` and `aud` from the
forwarded request URL, and the gateway sets `X-Forwarded-Port`, so every token
reads `https://usersrole.<env>.jdwlabs.com:443/auth/authenticate`. The Go
verifiers compare exactly. Without the port they refuse every JVM token with
401 (proven in non pre-flight). The JVM does not check `iss` at all, which is
how this stayed hidden.

## Steps

Each step is non first, then prd after the soak passes. One values change per
step, in one PR.

| Step | Prefixes | To | Values change |
|---|---|---|---|
| 1 | `/api/profiles` | profile-service | `ingress` block in `charts/profile-service/values-<env>.yaml` |
| 2 | `/api/users`, `/api/roles` | identity-service | `ingress` block in `charts/identity-service/values-<env>.yaml` |
| 3 | `/auth` | identity-service | add a `/auth` match to that same block |

Profiles go first because the context is separable: its three tables are
touched by nothing else. `/auth` goes last because a failure there signs
everyone out.

The step-1 block (step 2 has the same shape with its own prefixes):

```yaml
ingress:
  enabled: true
  gateway: { name: platform-gateway, namespace: nginx-gateway, sectionName: https }
  annotations: { cert-manager.io/cluster-issuer: letsencrypt-prod }
  hosts:
    - host: usersrole.<env>.jdwlabs.com
  rules:
    - matches:
        - path: { type: PathPrefix, value: /api/profiles }
```

`hosts` must always be set alongside `enabled: true`. An HTTPRoute with no
hostnames attaches to every host on the listener.

## Verify a step

1. The route is accepted: `kubectl -n jdwlabs-<env> get httproute
   jdwlabs-<svc>-<env> -o jsonpath='{.status.parents[0].conditions}'` shows
   `Accepted=True` and `ResolvedRefs=True`.
2. Requests land on the Go service. Its logs show the request lines (the JVM's
   `http_server_requests_seconds_count` for that `uri` stops rising), and
   `/actuator/health` on the hostname still answers from the JVM.
3. **non only:** run the parity harness through the gateway. See below.
   `platform-e2e` does not prove a backend step: it mocks every backend call
   (`sign-in-as.ts`, `page.route`).

### Parity harness (non)

`tools/cutover-parity.py` runs every profile operation twice, once per
backend, each with a fresh throwaway user it registers through the JVM, and
diffs status codes and normalised bodies. It mints tokens through the real
gateway so `iss` is exactly what browsers get. This default mode registers
users, so it refuses prd URLs; prd uses the account mode below.

```sh
kubectl -n nginx-gateway port-forward svc/platform-gateway-nginx 18443:443 &
kubectl -n jdwlabs-non port-forward svc/jdwlabs-usersrole-non 18080:8080 &
kubectl -n jdwlabs-non port-forward svc/jdwlabs-profile-service-non 18081:8080 &
python3 tools/cutover-parity.py --via-gateway 18443 \
  https://usersrole.non.jdwlabs.com http://localhost:18080 http://localhost:18081
```

After the step, pass the public hostname as the second backend, too. Both
columns should then read Go.

Known, accepted differences (JVM → Go):

- `birthdate` is `1990-01-02T00:00:00.000Z` → `1990-01-02`. The Go form is the
  correct one: the profile page's `MomentDateAdapter` reads the JVM form as a
  UTC instant and shows the day before in any US timezone. The grid's filter
  and sort parse both forms to the same instant.
- Reading your own profile right after deleting it, with a token that still
  carries its `profile_id`: 403 → 404. Go authorises from the claim, finds
  nothing and says so.

## Soak and abort criteria

Hold each step for at least 24h in non, then at least 48h in prd, before the
next step. Abort (roll back) on any of:

- any 5xx from the Go service on a moved path
- any 401 from the Go service with `a presented token did not verify` in its
  logs
- p95 or p99 for the moved `uri` above the JVM's figure for the same `uri`,
  from `http_server_requests_seconds` on both sides
- any authorisation anomaly: a 200 where the JVM gave 403, or the reverse,
  outside the two differences above

prd carries almost no organic API traffic: 7 days before step 1, the JVM saw
445 requests, all `uri=UNKNOWN`. A latency comparison there needs synthetic
load from a real account.

### Synthetic load (prd)

The same harness with `--account` logs in as one pre-existing test account
instead of registering users. Create that account once, outside any agent
session (the password is a credential): `POST /auth/user` on the prd host,
default role only, no ADMIN, so the admin-only 403 is exercised. Keep the
credential in Vault and export it only in the shell that runs the load:

```sh
export PARITY_ACCOUNT_EMAIL=... PARITY_ACCOUNT_PASSWORD=...
# or --account-file PATH: JSON {"emailAddress": ..., "password": ...}, mode 0600
```

Each cycle creates the account's profile, exercises all profile operations on
it, and deletes it. It stops before any write if the profile already exists,
and deletes it on the way out if a cycle dies midway. `--single` checks one
backend against the expected status table; response bodies are never printed
in account mode. Rehearse with `--dry-run` first: it prints the request
sequence and schedule and sends nothing.

```sh
# B0 before the step, B1 after it: 1 cycle / 10s for 60 min, cap 360
python3 tools/cutover-parity.py --env prd --account --single --preset baseline \
  --csv b0.csv https://usersrole.prd.jdwlabs.com https://usersrole.prd.jdwlabs.com
# soak canary: 1 cycle / 5 min, cap 600, bounded by a deadline
python3 tools/cutover-parity.py --env prd --account --single --preset soak \
  --deadline "$(date -u -d "+48 hours" +%FT%TZ)" --csv soak.csv \
  https://usersrole.prd.jdwlabs.com https://usersrole.prd.jdwlabs.com
```

The run exits non-zero after `--max-consecutive-failures` failed cycles in a
row (default 1): any status off the table, any 5xx, any timeout. The CSV holds
client-side latency per operation, measured at the same point before and
after the step, so it is comparable where the two sides' histogram buckets
are not.

## Rollback

Revert the step's PR. ArgoCD prunes the Go route within one sync, and the JVM's
catch-all takes the path back. Tokens are interchangeable in both directions
because the issuer origin matches, so nobody is signed out.

Rehearsed in non: see the step-1 record below.

## Decommission gate

`usersrole` is removed only when every line below reads pass,
with the observed value beside it:

- [ ] all three steps live in prd, each soak passed
- [ ] zero requests to the JVM on the hostname for 7 consecutive days
      (`sum(increase(http_server_requests_seconds_count{namespace="jdwlabs-prd",container="usersrole",uri!~"/actuator.*"}[7d]))`,
      excluding `uri=UNKNOWN` scanner noise)
- [ ] rollback rehearsed in non with its recovery time recorded
- [ ] headline metric recorded: combined working set of the two Go services
      against the JVM, from the same query

Baseline, 24h max of `container_memory_working_set_bytes` on 2026-09-23:

| | non | prd |
|---|---|---|
| usersrole | 267 MiB | 238 MiB |
| identity-service + profile-service | 18.6 MiB | 18.3 MiB |

## Record

| Step | env | Live | Verified | Rolled back |
|---|---|---|---|---|
| 1 | non | | | |
