#!/usr/bin/env python3
"""Profile-path parity and synthetic load for the usersrole cutover
(docs/usersrole-cutover.md).

Default (non only): runs every /api/profiles operation against two backends,
each with its own throwaway user registered through the JVM's /auth endpoints,
and diffs status codes and normalised bodies.

    cutover-parity.py [--via-gateway PORT] AUTH_URL A_URL B_URL

Account mode (--account): logs in as one pre-existing, low-privilege test
account instead of registering users, so it is the only mode allowed in prd.
Credentials come from PARITY_ACCOUNT_EMAIL / PARITY_ACCOUNT_PASSWORD or from
--account-file (JSON with emailAddress and password, mode 0600). Every write
goes to the account's own profile, which each cycle creates and deletes.

    cutover-parity.py --env prd --account --single --preset baseline \\
        --csv b0.csv AUTH_URL BASE_URL

--single checks one backend against EXPECTED instead of diffing two. The loop
flags (--interval, --max-cycles, --duration, --deadline,
--max-consecutive-failures) pace repeated cycles under hard caps; --dry-run
prints the request sequence and schedule without sending anything.

--via-gateway resolves *.<env>.jdwlabs.com:443 to a local port-forward of the
gateway, so tokens are minted with the real Host and forwarded headers (and so
the same iss a browser gets) without hairpinning through the WAN address.
"""
import argparse
import base64
import contextlib
import csv
import json
import os
import re
import secrets
import socket
import stat
import struct
import sys
import time
import zlib
from datetime import datetime, timezone

TIMEOUT = 20
MAX_CYCLES_CEILING = 1000
ACCOUNT_ENV = ("PARITY_ACCOUNT_EMAIL", "PARITY_ACCOUNT_PASSWORD")

# Rates from the prd step-1 load plan: B0/B1 at 1 cycle per 10s for an hour,
# then a 48h soak canary at 1 cycle per 5 minutes.
PRESETS = {
    "baseline": {"interval": 10.0, "duration": 3600.0, "max_cycles": 360},
    "soak": {"interval": 300.0, "duration": 48 * 3600.0, "max_cycles": 600},
}

# The two differences the runbook records as accepted; anything else fails the run.
ACCEPTED = {
    "GET by-id after delete": ((403, 404),),
}

# Single-backend expectations, observed identical on the JVM and Go in non
# except for the accepted 403 -> 404 after delete. Rows with status 0 are
# harness checks, not requests; they pass when their value is truthy.
EXPECTED = {
    "no-token GET by-user": {401},
    "GET by-user before create": {404},
    "POST profile": {201},
    "token profile claim present": {0},
    "GET by-user": {200},
    "GET by-id": {200},
    "GET other by-user": {403},
    "GET all (non-admin)": {403},
    "PUT by-id": {200},
    "PUT by-user invalid": {400},
    "POST address": {200},
    "PUT address": {200},
    "DELETE address": {204},
    "POST icon": {200},
    "GET icon": {200},
    "icon roundtrip bytes equal": {0},
    "PUT icon": {200},
    "DELETE icon": {204},
    "DELETE by-id": {204},
    "GET by-id after delete": {403, 404},
    "CORS preflight": {200},
}


class Abort(Exception):
    """Stops the whole run regardless of --max-consecutive-failures."""


class Account:
    def __init__(self, email, password):
        self.email, self.password = email, password

    def __repr__(self):
        return "Account(<redacted>)"


def load_account(path=None, environ=os.environ):
    if path:
        mode = os.stat(path).st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SystemExit(f"{path} is readable by group or others; chmod 600 it first")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        email, password = data.get("emailAddress"), data.get("password")
        if not email or not password:
            raise SystemExit(f"{path} needs both emailAddress and password")
        return Account(email, password)
    missing = [k for k in ACCOUNT_ENV if not environ.get(k)]
    if missing:
        raise SystemExit(f"--account needs {', '.join(missing)} set (or --account-file)")
    return Account(*(environ[k] for k in ACCOUNT_ENV))


def diff_unexpected(ra, rb):
    bad = 0
    for (name, sa, ba), (_, sb, bb) in zip(ra, rb):
        if sa == sb and _strip_birthdate(ba) == _strip_birthdate(bb):
            continue
        if (sa, sb) in ACCEPTED.get(name, ()):
            continue
        bad += 1
    return bad


def check_expected(results):
    """Names of results that break EXPECTED, plus any 5xx."""
    bad = []
    for name, status, body in results:
        if status >= 500 or status not in EXPECTED.get(name, ()) or (status == 0 and not body):
            bad.append(name)
    return bad


def _strip_birthdate(v):
    if isinstance(v, dict):
        return {k: (x[:10] if k == "birthdate" and isinstance(x, str) else _strip_birthdate(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip_birthdate(x) for x in v]
    return v


def _chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
PNG = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
       + _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + _chunk(b"IEND", b""))

def claims(tok):
    p = tok.split(".")[1]; return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))

VOLATILE = re.compile(r"(?i)^(id|.*Id|.*_id|created.*|modified.*|updated.*|emailAddress|createdBy|modifiedBy|timestamp|path|trace.*)$")
def norm(v):
    if isinstance(v, dict): return {k: ("<v>" if VOLATILE.match(k) and not isinstance(v[k], (dict, list)) else norm(x)) for k, x in sorted(v.items())}
    if isinstance(v, list): return [norm(x) for x in v]
    return v


class DryRunResponse:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content
        self.ok = status < 400
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


def _fake_token(profile_id):
    body = base64.urlsafe_b64encode(json.dumps({"user_id": "{user_id}", "profile_id": profile_id}).encode()).decode()
    return f"x.{body.rstrip('=')}.x"


class DryRunSession:
    """Prints each request instead of sending it and answers with the expected
    status, so the printed sequence is exactly the one a real cycle walks."""

    def __init__(self, out):
        self.out, self.profile = out, None

    def request(self, method, url, name=None, headers=None, json=None, files=None, **_):
        parts = [f"  {method:7} {url}"]
        if headers and "Authorization" in headers:
            parts.append("[bearer]")
        if json is not None:
            parts.append("body keys=" + ",".join(sorted(json)))
        if files:
            parts.append("multipart=" + ",".join(files))
        print(" ".join(parts), file=self.out)
        if name == "login":
            return DryRunResponse(200, {"jwtToken": _fake_token(self.profile)})
        if name == "register":
            return DryRunResponse(201, {"id": "{user_id}"})
        status = min(EXPECTED.get(name, {200}))
        if name == "POST profile":
            self.profile = "{profile_id}"
            return DryRunResponse(status, {"id": "{profile_id}"})
        if name == "POST address":
            return DryRunResponse(status, {"addresses": [{"id": "{address_id}"}]})
        if name == "GET icon":
            return DryRunResponse(status, content=PNG)
        if name == "DELETE by-id":
            self.profile = None
        return DryRunResponse(status)


class RequestsSession:
    def __init__(self):
        import requests
        self._s = requests.Session()

    def request(self, method, url, name=None, **kw):
        return self._s.request(method, url, **kw)


class Harness:
    def __init__(self, session, auth, env="non", account=None, csv_writer=None, out=sys.stdout):
        self.session, self.auth, self.env = session, auth, env
        self.account, self.csv, self.out = account, csv_writer, out
        self.cycle = 0

    def http(self, label, name, method, url, **kw):
        t0 = time.monotonic()
        r = self.session.request(method, url, name=name, timeout=TIMEOUT, **kw)
        if self.csv and name != "register":
            self.csv.writerow([datetime.now(timezone.utc).isoformat(timespec="milliseconds"), self.cycle, label,
                               name, method, r.status_code, round((time.monotonic() - t0) * 1000, 2)])
        return r

    def login_fn(self, label):
        if self.account:
            email, pw = self.account.email, self.account.password
        else:
            email = f"parity-{secrets.token_hex(4)}@example.com"
            pw = "Pa1!" + secrets.token_urlsafe(12)
            r = self.http(label, "register", "POST", f"{self.auth}/auth/user", json={"emailAddress": email, "password": pw})
            assert r.status_code == 201, (r.status_code, r.text[:300])
            uid = r.json()["id"]

        def login():
            r = self.http(label, "login", "POST", f"{self.auth}/auth/authenticate", json={"emailAddress": email, "password": pw})
            if r.status_code != 200:
                # Status only: the body can echo the submitted email.
                raise Abort(f"login failed with {r.status_code}")
            return r.json()["jwtToken"]

        if self.account:
            tok = login()
            return claims(tok)["user_id"], login, tok
        return uid, login, None

    def run(self, base, label="A"):
        uid, login, tok = self.login_fn(label)
        tok = tok or login(); h = lambda t: {"Authorization": f"Bearer {t}"}
        out = []
        def rec(name, method, url, **kw):
            r = self.http(label, name, method, url, **kw)
            try: body = norm(r.json())
            except Exception: body = f"<{r.headers.get('content-type','')} {len(r.content)}B>"
            out.append((name, r.status_code, body))
            return r
        rec("no-token GET by-user", "GET", f"{base}/api/profiles/by-user/{uid}")
        r = rec("GET by-user before create", "GET", f"{base}/api/profiles/by-user/{uid}", headers=h(tok))
        if self.account and r.status_code != 404:
            raise Abort(f"leftover profile guard: GET by-user returned {r.status_code}, expected 404; clean up the test account before rerunning")
        pid, live = None, False
        try:
            r = rec("POST profile", "POST", f"{base}/api/profiles", headers=h(tok),
                    json={"firstName": "Parity", "lastName": "Check", "birthdate": "1990-01-02", "userId": uid})
            pid = r.json().get("id") if r.ok else None
            live = pid is not None
            tok = login(); c = claims(tok)
            out.append(("token profile claim present", 0, sorted(k for k in c if "profile" in k.lower())))
            other = 2 if str(uid) == "1" else 1
            rec("GET by-user", "GET", f"{base}/api/profiles/by-user/{uid}", headers=h(tok))
            rec("GET by-id", "GET", f"{base}/api/profiles/{pid}", headers=h(tok))
            rec("GET other by-user", "GET", f"{base}/api/profiles/by-user/{other}", headers=h(tok))
            rec("GET all (non-admin)", "GET", f"{base}/api/profiles", headers=h(tok))
            rec("PUT by-id", "PUT", f"{base}/api/profiles/{pid}", headers=h(tok),
                json={"firstName": "Parity2", "middleName": "M", "lastName": "Check", "birthdate": "1990-01-03"})
            rec("PUT by-user invalid", "PUT", f"{base}/api/profiles/by-user/{uid}", headers=h(tok),
                json={"firstName": "", "lastName": "Check", "birthdate": "2999-01-01"})
            r = rec("POST address", "POST", f"{base}/api/profiles/{pid}/address", headers=h(tok),
                    json={"addressLine1": "1 Test St", "city": "Town", "stateProvince": "ST", "postalCode": "12345", "country": "US"})
            addrs = (r.json().get("addresses") or []) if r.ok else []
            aid = addrs[0].get("id") if addrs else 0
            rec("PUT address", "PUT", f"{base}/api/profiles/{pid}/address/{aid}", headers=h(tok),
                json={"addressLine1": "2 Test St", "city": "Town", "stateProvince": "ST", "postalCode": "12345", "country": "US"})
            rec("DELETE address", "DELETE", f"{base}/api/profiles/{pid}/address/{aid}", headers=h(tok))
            rec("POST icon", "POST", f"{base}/api/profiles/{pid}/icon", headers=h(tok), files={"icon": ("i.png", PNG, "image/png")})
            r = rec("GET icon", "GET", f"{base}/api/profiles/{pid}/icon", headers=h(tok))
            out.append(("icon roundtrip bytes equal", 0, r.content == PNG))
            rec("PUT icon", "PUT", f"{base}/api/profiles/{pid}/icon", headers=h(tok), files={"icon": ("i.png", PNG, "image/png")})
            rec("DELETE icon", "DELETE", f"{base}/api/profiles/{pid}/icon", headers=h(tok))
            r = rec("DELETE by-id", "DELETE", f"{base}/api/profiles/{pid}", headers=h(tok))
            live = live and not r.ok
            rec("GET by-id after delete", "GET", f"{base}/api/profiles/{pid}", headers=h(tok))
            pre = self.http(label, "CORS preflight", "OPTIONS", f"{base}/api/profiles/{pid}", headers={
                "Origin": f"https://authui.{self.env}.jdwlabs.com",
                "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "authorization,content-type"})
            out.append(("CORS preflight", pre.status_code, {k.lower(): v for k, v in pre.headers.items() if k.lower().startswith("access-control-allow-origin")}))
        finally:
            if self.account and live:
                self._cleanup(label, base, pid, login)
        return out

    def _cleanup(self, label, base, pid, login):
        try:
            r = self.http(label, "cleanup DELETE by-id", "DELETE", f"{base}/api/profiles/{pid}",
                          headers={"Authorization": f"Bearer {login()}"})
            print(f"cleanup: DELETE profile -> {r.status_code}", file=self.out)
        except Exception as e:
            print(f"cleanup: DELETE profile failed ({type(e).__name__}); the next cycle's guard will stop the run", file=self.out)


def install_gateway_resolver(port, env):
    _getaddrinfo = socket.getaddrinfo

    def _gateway_getaddrinfo(host, p, *rest, **kw):
        if host.endswith(f".{env}.jdwlabs.com") and p == 443:
            return _getaddrinfo("127.0.0.1", port, *rest, **kw)
        return _getaddrinfo(host, p, *rest, **kw)

    socket.getaddrinfo = _gateway_getaddrinfo


def parse_deadline(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--via-gateway", type=int, metavar="PORT")
    p.add_argument("--env", choices=("non", "prd"), default="non", help="gateway domain and CORS origin (default non)")
    p.add_argument("--account", action="store_true", help="log in as the pre-existing test account; never register users")
    p.add_argument("--account-file", metavar="PATH", help="JSON credentials file (implies --account)")
    p.add_argument("--single", action="store_true", help="one backend, statuses checked against EXPECTED")
    p.add_argument("--preset", choices=sorted(PRESETS), help="baseline: 10s x 60min, cap 360; soak: 5min x 48h, cap 600")
    p.add_argument("--interval", type=float, metavar="SEC", help="seconds between cycle starts")
    p.add_argument("--max-cycles", type=int, metavar="N", help=f"hard cap on cycles (default 1, ceiling {MAX_CYCLES_CEILING})")
    p.add_argument("--duration", type=float, metavar="SEC", help="stop starting cycles after this many seconds")
    p.add_argument("--deadline", type=parse_deadline, metavar="UTC", help="stop starting cycles at this ISO-8601 time")
    p.add_argument("--max-consecutive-failures", type=int, default=1, metavar="N", help="stop after N failed cycles in a row (default 1)")
    p.add_argument("--csv", metavar="PATH", help="append per-request client-side latency rows")
    p.add_argument("--dry-run", action="store_true", help="print the request sequence and schedule; send nothing")
    p.add_argument("--verbose", action="store_true", help="--single: print every operation, not only failures")
    p.add_argument("auth")
    p.add_argument("bases", nargs="+", metavar="BASE")
    a = p.parse_args(argv)
    a.account = a.account or bool(a.account_file)

    preset = PRESETS.get(a.preset, {})
    for k in ("interval", "duration", "max_cycles"):
        if getattr(a, k) is None:
            setattr(a, k, preset.get(k))
    a.interval = a.interval or 0.0
    a.max_cycles = a.max_cycles or 1

    if len(a.bases) != (1 if a.single else 2):
        p.error("--single takes AUTH_URL BASE_URL; otherwise AUTH_URL A_URL B_URL")
    if a.env == "prd" and not a.account:
        p.error("--env prd requires --account: the default mode registers throwaway users")
    if not a.account and any(".prd." in u for u in [a.auth, *a.bases]):
        p.error("prd URLs require --account: the default mode registers throwaway users")
    if a.env != "prd" and any(".prd." in u for u in [a.auth, *a.bases]):
        p.error("prd URLs need --env prd, so the gateway resolver and CORS origin match")
    if not 1 <= a.max_cycles <= MAX_CYCLES_CEILING:
        p.error(f"--max-cycles must be between 1 and {MAX_CYCLES_CEILING}")
    if a.max_cycles > 1 and a.interval < 1:
        p.error("more than one cycle needs --interval of at least 1 second")
    if a.max_consecutive_failures < 1:
        p.error("--max-consecutive-failures must be at least 1")
    return a


def ab_cycle(h, a_url, b_url, redact, out):
    ra, rb = h.run(a_url, "A"), h.run(b_url, "B")
    diff = 0
    for (n, sa, ba), (_, sb, bb) in zip(ra, rb):
        same = sa == sb and ba == bb
        diff += not same
        print(("OK  " if same else "DIFF"), f"{n:28} A={sa} B={sb}", file=out)
        if not same and not redact:
            print("     A:", json.dumps(ba)[:400], file=out); print("     B:", json.dumps(bb)[:400], file=out)
    print(f"\n{len(ra)-diff}/{len(ra)} identical", file=out)
    return diff_unexpected(ra, rb) == 0


def single_cycle(h, base, verbose, out):
    results = h.run(base, "A")
    bad = check_expected(results)
    for name, status, _ in results:
        if verbose or name in bad:
            print(("OK  " if name not in bad else "FAIL"), f"{name:28} {status} expected {sorted(EXPECTED.get(name, ()))}", file=out)
    return not bad


def loop(cycle, *, interval, max_cycles, end_at, max_failures, clock, sleep, out):
    """Runs cycle(i) on a fixed-rate schedule under hard caps. end_at is on the
    clock's own scale. Returns the process exit code."""
    t0 = clock()
    failed = streak = done = 0
    reason = f"max cycles ({max_cycles})"
    for i in range(max_cycles):
        start = t0 + i * interval
        if end_at is not None and start >= end_at:
            reason = "duration/deadline reached"
            break
        wait = start - clock()
        if wait > 0:
            sleep(wait)
        try:
            ok = cycle(i + 1)
        except Abort as e:
            print(f"cycle {i + 1}: ABORT {e}", file=out)
            failed += 1; done += 1
            reason = "abort"
            break
        except Exception as e:
            print(f"cycle {i + 1}: ERROR {type(e).__name__}: {e}", file=out)
            ok = False
        done += 1
        if ok:
            streak = 0
        else:
            failed += 1; streak += 1
        if max_cycles > 1:
            print(f"cycle {i + 1}/{max_cycles}: {'ok' if ok else 'FAILED'}", file=out)
        if streak >= max_failures:
            reason = f"{streak} consecutive failed cycle(s)"
            break
    if max_cycles > 1 or failed:
        print(f"stopped: {reason}; {done} cycle(s), {failed} failed", file=out)
    return 1 if failed else 0


def main(argv=None, environ=os.environ, session=None, clock=time.monotonic, sleep=time.sleep, out=sys.stdout):
    a = parse_args(argv)
    end_at = None
    if a.duration:
        end_at = a.duration
    if a.deadline:
        until = (a.deadline - datetime.now(timezone.utc)).total_seconds()
        end_at = until if end_at is None else min(end_at, until)

    if a.dry_run:
        source = f"file {a.account_file}" if a.account_file else f"env {'/'.join(ACCOUNT_ENV)}"
        print(f"dry run: nothing is sent. env={a.env} mode={'single' if a.single else 'A/B diff'} "
              f"users={'test account from ' + source + ' (not read)' if a.account else 'registered per run'}", file=out)
        print(f"schedule: up to {a.max_cycles} cycle(s), every {a.interval:g}s"
              + (f", stop after {end_at:g}s" if end_at is not None else "")
              + f", stop on {a.max_consecutive_failures} consecutive failure(s)", file=out)
        account, session = (Account("<account>", "<password>") if a.account else None), DryRunSession(out)
    else:
        account = load_account(a.account_file, environ) if a.account else None
        session = session or RequestsSession()
        if a.via_gateway:
            install_gateway_resolver(a.via_gateway, a.env)

    csv_path = a.csv if a.csv and not a.dry_run else None
    with open(csv_path, "a", newline="", encoding="utf-8") if csv_path else contextlib.nullcontext() as csv_file:
        writer = None
        if csv_file:
            writer = csv.writer(csv_file)
            if csv_file.tell() == 0:
                writer.writerow(["utc", "cycle", "backend", "op", "method", "status", "latency_ms"])
        h = Harness(session, a.auth, a.env, account, csv_writer=writer, out=out)

        def cycle(i):
            h.cycle = i
            if a.dry_run:
                print(f"cycle {i} request sequence:", file=out)
            ok = single_cycle(h, a.bases[0], a.verbose, out) if a.single else ab_cycle(h, *a.bases, bool(account), out)
            if csv_file:
                csv_file.flush()
            return ok

        if a.dry_run:
            cycle(1)
            return 0
        return loop(cycle, interval=a.interval, max_cycles=a.max_cycles, end_at=end_at,
                    max_failures=a.max_consecutive_failures, clock=clock, sleep=sleep, out=out)

if __name__ == "__main__":
    sys.exit(main())
