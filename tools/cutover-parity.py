#!/usr/bin/env python3
"""Profile-path parity for the usersrole cutover (docs/usersrole-cutover.md).

Runs every /api/profiles operation against two backends, each with its own
throwaway user registered through the JVM's /auth endpoints, and diffs status
codes and normalised bodies. Creates users on every run, so non only.

usage: cutover-parity.py [--via-gateway PORT] AUTH_URL A_URL B_URL

--via-gateway resolves *.non.jdwlabs.com:443 to a local port-forward of the
gateway, so tokens are minted with the real Host and forwarded headers (and so
the same iss a browser gets) without hairpinning through the WAN address.
"""
import argparse
import base64
import json
import re
import secrets
import socket
import struct
import sys
import zlib

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--via-gateway", type=int, metavar="PORT")
parser.add_argument("auth")
parser.add_argument("a")
parser.add_argument("b")
args = parser.parse_args()
AUTH, A, B = args.auth, args.a, args.b

if args.via_gateway:
    _getaddrinfo = socket.getaddrinfo

    def _gateway_getaddrinfo(host, port, *rest, **kw):
        if host.endswith(".non.jdwlabs.com") and port == 443:
            return _getaddrinfo("127.0.0.1", args.via_gateway, *rest, **kw)
        return _getaddrinfo(host, port, *rest, **kw)

    socket.getaddrinfo = _gateway_getaddrinfo

# The two differences the runbook records as accepted; anything else fails the run.
ACCEPTED = {
    "GET by-id after delete": ((403, 404),),
}


def diff_unexpected(ra, rb):
    bad = 0
    for (name, sa, ba), (_, sb, bb) in zip(ra, rb):
        if sa == sb and _strip_birthdate(ba) == _strip_birthdate(bb):
            continue
        if (sa, sb) in ACCEPTED.get(name, ()):
            continue
        bad += 1
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

def user():
    email = f"parity-{secrets.token_hex(4)}@example.com"
    pw = "Pa1!" + secrets.token_urlsafe(12)
    r = requests.post(f"{AUTH}/auth/user", json={"emailAddress": email, "password": pw}, timeout=20)
    assert r.status_code == 201, (r.status_code, r.text[:300])
    login = lambda: requests.post(f"{AUTH}/auth/authenticate", json={"emailAddress": email, "password": pw}, timeout=20).json()["jwtToken"]
    return email, r.json()["id"], login

VOLATILE = re.compile(r"(?i)^(id|.*Id|.*_id|created.*|modified.*|updated.*|emailAddress|createdBy|modifiedBy|timestamp|path|trace.*)$")
def norm(v):
    if isinstance(v, dict): return {k: ("<v>" if VOLATILE.match(k) and not isinstance(v[k], (dict, list)) else norm(x)) for k, x in sorted(v.items())}
    if isinstance(v, list): return [norm(x) for x in v]
    return v

def run(base):
    email, uid, login = user()
    tok = login(); h = lambda t: {"Authorization": f"Bearer {t}"}
    out = []
    def rec(name, r):
        try: body = norm(r.json())
        except Exception: body = f"<{r.headers.get('content-type','')} {len(r.content)}B>"
        out.append((name, r.status_code, body))
        return r
    rec("no-token GET by-user", requests.get(f"{base}/api/profiles/by-user/{uid}", timeout=20))
    rec("GET by-user before create", requests.get(f"{base}/api/profiles/by-user/{uid}", headers=h(tok), timeout=20))
    r = rec("POST profile", requests.post(f"{base}/api/profiles", headers=h(tok), timeout=20,
            json={"firstName": "Parity", "lastName": "Check", "birthdate": "1990-01-02", "userId": uid}))
    pid = r.json().get("id") if r.ok else None
    tok = login(); c = claims(tok)
    out.append(("token profile claim present", 0, sorted(k for k in c if "profile" in k.lower())))
    rec("GET by-user", requests.get(f"{base}/api/profiles/by-user/{uid}", headers=h(tok), timeout=20))
    rec("GET by-id", requests.get(f"{base}/api/profiles/{pid}", headers=h(tok), timeout=20))
    rec("GET other by-user", requests.get(f"{base}/api/profiles/by-user/1", headers=h(tok), timeout=20))
    rec("GET all (non-admin)", requests.get(f"{base}/api/profiles", headers=h(tok), timeout=20))
    rec("PUT by-id", requests.put(f"{base}/api/profiles/{pid}", headers=h(tok), timeout=20,
        json={"firstName": "Parity2", "middleName": "M", "lastName": "Check", "birthdate": "1990-01-03"}))
    rec("PUT by-user invalid", requests.put(f"{base}/api/profiles/by-user/{uid}", headers=h(tok), timeout=20,
        json={"firstName": "", "lastName": "Check", "birthdate": "2999-01-01"}))
    r = rec("POST address", requests.post(f"{base}/api/profiles/{pid}/address", headers=h(tok), timeout=20,
        json={"addressLine1": "1 Test St", "city": "Town", "stateProvince": "ST", "postalCode": "12345", "country": "US"}))
    addrs = (r.json().get("addresses") or []) if r.ok else []
    aid = addrs[0].get("id") if addrs else 0
    rec("PUT address", requests.put(f"{base}/api/profiles/{pid}/address/{aid}", headers=h(tok), timeout=20,
        json={"addressLine1": "2 Test St", "city": "Town", "stateProvince": "ST", "postalCode": "12345", "country": "US"}))
    rec("DELETE address", requests.delete(f"{base}/api/profiles/{pid}/address/{aid}", headers=h(tok), timeout=20))
    rec("POST icon", requests.post(f"{base}/api/profiles/{pid}/icon", headers=h(tok), timeout=20, files={"icon": ("i.png", PNG, "image/png")}))
    r = rec("GET icon", requests.get(f"{base}/api/profiles/{pid}/icon", headers=h(tok), timeout=20))
    out.append(("icon roundtrip bytes equal", 0, r.content == PNG))
    rec("PUT icon", requests.put(f"{base}/api/profiles/{pid}/icon", headers=h(tok), timeout=20, files={"icon": ("i.png", PNG, "image/png")}))
    rec("DELETE icon", requests.delete(f"{base}/api/profiles/{pid}/icon", headers=h(tok), timeout=20))
    rec("DELETE by-id", requests.delete(f"{base}/api/profiles/{pid}", headers=h(tok), timeout=20))
    rec("GET by-id after delete", requests.get(f"{base}/api/profiles/{pid}", headers=h(tok), timeout=20))
    pre = requests.options(f"{base}/api/profiles/{pid}", timeout=20, headers={"Origin": "https://authui.non.jdwlabs.com",
        "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "authorization,content-type"})
    out.append(("CORS preflight", pre.status_code, {k.lower(): v for k, v in pre.headers.items() if k.lower().startswith("access-control-allow-origin")}))
    return out


def main():
    ra, rb = run(A), run(B)
    diff = 0
    for (n, sa, ba), (_, sb, bb) in zip(ra, rb):
        same = sa == sb and ba == bb
        diff += not same
        print(("OK  " if same else "DIFF"), f"{n:28} A={sa} B={sb}")
        if not same:
            print("     A:", json.dumps(ba)[:400]); print("     B:", json.dumps(bb)[:400])
    print(f"\n{len(ra)-diff}/{len(ra)} identical")
    return 1 if diff_unexpected(ra, rb) else 0


if __name__ == "__main__":
    sys.exit(main())
