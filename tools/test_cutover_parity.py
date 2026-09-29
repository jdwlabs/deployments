#!/usr/bin/env python3
"""Unit tests for tools/cutover-parity.py.

The account and loop modes exist to run against prd, so these pin the ways
they could do harm there: registering users, leaking the credential, leaving a
profile behind, or running past a cap. Everything talks to an in-memory fake
of the profile API; nothing touches the network. Run with:

    python3 -m unittest discover -s tools -p 'test_*.py'
"""

import base64
import importlib.util
import io
import json
import re
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_spec = importlib.util.spec_from_file_location("cutover_parity", Path(__file__).with_name("cutover-parity.py"))
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

EMAIL, PASSWORD = "loadtest@example.test", "s3cret-Not-For-Output"
ENV = {"PARITY_ACCOUNT_EMAIL": EMAIL, "PARITY_ACCOUNT_PASSWORD": PASSWORD}
AUTH, BASE = "https://usersrole.prd.jdwlabs.com", "https://usersrole.prd.jdwlabs.com"


class Resp:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._payload, self.ok = status, payload, status < 400
        self.content = content if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode(errors="replace")
        self.headers = {"content-type": "application/json" if payload is not None else "image/png"}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def token(uid, pid):
    body = base64.urlsafe_b64encode(json.dumps({"user_id": uid, "profile_id": pid}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class FakeApi:
    """Enough of /auth and /api/profiles for one account to walk a cycle."""

    UID = 42

    def __init__(self, leftover=False, fail=None, raise_on=None, fail_host=""):
        self.calls, self.profile, self.icon = [], ({"id": 7} if leftover else None), None
        self.fail, self.raise_on, self.fail_host = fail or {}, raise_on, fail_host

    def request(self, method, url, name=None, headers=None, json=None, files=None, timeout=None):
        path = re.sub(r"^https?://[^/]+", "", url)
        self.calls.append((method, path, name))
        if name == self.raise_on:
            raise TimeoutError("read timed out")
        if name in self.fail and self.fail_host in url:
            return Resp(self.fail[name], {"error": "x"})
        if path == "/auth/user":
            return Resp(201, {"id": 99})
        if path == "/auth/authenticate":
            ok = json == {"emailAddress": EMAIL, "password": PASSWORD}
            pid = self.profile["id"] if self.profile else None
            return Resp(200, {"jwtToken": token(self.UID, pid)}) if ok else Resp(401, {"error": EMAIL})
        if method == "OPTIONS":
            return Resp(200, {})
        authed = bool(headers and "Authorization" in headers)
        if not authed:
            return Resp(401, {"error": "unauthorized"})
        if path == "/api/profiles" and method == "GET":
            return Resp(403, {"error": "forbidden"})
        if path == "/api/profiles" and method == "POST":
            self.profile = {"id": 7, "addresses": []}
            return Resp(201, self.profile)
        if m := re.fullmatch(r"/api/profiles/by-user/(\w+)", path):
            if m.group(1) != str(self.UID):
                return Resp(403, {"error": "forbidden"})
            if method == "PUT":
                return Resp(400, {"error": "invalid"})
            return Resp(200, self.profile) if self.profile else Resp(404, {"error": "not found"})
        if not self.profile:
            return Resp(404, {"error": "not found"})
        if path.endswith("/icon"):
            if method == "GET":
                return Resp(200, content=self.icon)
            self.icon = None if method == "DELETE" else cp.PNG
            return Resp(204) if method == "DELETE" else Resp(200, self.profile)
        if "/address" in path:
            if method == "POST":
                return Resp(200, {**self.profile, "addresses": [{"id": 3}]})
            return Resp(204) if method == "DELETE" else Resp(200, self.profile)
        if method == "DELETE":
            self.profile = None
            return Resp(204)
        return Resp(200, self.profile)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def run(argv, api=None, environ=ENV, clock=None):
    out = io.StringIO()
    clock = clock or Clock()
    code = cp.main(argv, environ=environ, session=api or FakeApi(), clock=clock, sleep=clock.sleep, out=out)
    return code, out.getvalue()


PRD_SINGLE = ["--env", "prd", "--account", "--single", AUTH, BASE]


class AccountModeTest(unittest.TestCase):
    def test_single_cycle_passes_against_expected_table(self):
        api = FakeApi()
        code, out = run(PRD_SINGLE, api)
        self.assertEqual(code, 0, out)
        self.assertEqual(len([c for c in api.calls if c[2] == "login"]), 2)
        self.assertEqual(len([c for c in api.calls if c[0] != "OPTIONS" and c[1].startswith("/api/profiles")]), 18)

    def test_never_registers_a_user(self):
        api = FakeApi()
        run(PRD_SINGLE, api)
        self.assertNotIn("/auth/user", [c[1] for c in api.calls])

    def test_credential_never_reaches_output_even_when_login_fails(self):
        for env in (ENV, {**ENV, "PARITY_ACCOUNT_PASSWORD": "wrong-" + PASSWORD}):
            code, out = run(PRD_SINGLE, FakeApi(), environ=env)
            self.assertNotIn(PASSWORD, out)
            self.assertNotIn(EMAIL, out)
        self.assertEqual(code, 1)
        self.assertIn("login failed with 401", out)

    def test_missing_credentials_name_the_variables_only(self):
        with self.assertRaises(SystemExit) as e:
            run(PRD_SINGLE, environ={"PARITY_ACCOUNT_EMAIL": EMAIL})
        self.assertIn("PARITY_ACCOUNT_PASSWORD", str(e.exception))
        self.assertNotIn(EMAIL, str(e.exception))

    def test_account_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "acct.json"
            f.write_text(json.dumps({"emailAddress": EMAIL, "password": PASSWORD}))
            f.chmod(0o644)
            with self.assertRaises(SystemExit):
                cp.load_account(str(f), {})
            f.chmod(0o600)
            acct = cp.load_account(str(f), {})
            self.assertEqual((acct.email, acct.password), (EMAIL, PASSWORD))
            self.assertNotIn(PASSWORD, repr(acct))

    def test_leftover_profile_stops_the_run_before_any_write(self):
        api = FakeApi(leftover=True)
        code, out = run(PRD_SINGLE + ["--interval", "10", "--max-cycles", "5", "--max-consecutive-failures", "3"], api)
        self.assertEqual(code, 1)
        self.assertIn("leftover profile guard", out)
        self.assertFalse([c for c in api.calls if c[0] in ("POST", "PUT", "DELETE") and c[1].startswith("/api")])

    def test_profile_is_deleted_when_a_cycle_dies_midway(self):
        api = FakeApi(raise_on="PUT by-id")
        code, _ = run(PRD_SINGLE, api)
        self.assertEqual(code, 1)
        self.assertIn(("DELETE", "/api/profiles/7", "cleanup DELETE by-id"), api.calls)
        self.assertIsNone(api.profile)

    def test_writes_stay_on_the_accounts_own_profile(self):
        api = FakeApi()
        run(PRD_SINGLE, api)
        for method, path, name in api.calls:
            if method in ("POST", "PUT", "DELETE") and path.startswith("/api/profiles/"):
                self.assertRegex(path, rf"^/api/profiles/(7(/|$)|by-user/{FakeApi.UID}$)", name)

    def test_unexpected_status_and_5xx_fail_the_cycle(self):
        for status in (200, 503):
            code, out = run(PRD_SINGLE, FakeApi(fail={"GET other by-user": status}))
            self.assertEqual(code, 1)
            self.assertIn("FAIL GET other by-user", out)

    def test_bodies_are_not_printed_in_account_mode(self):
        api = FakeApi(fail={"GET other by-user": 200}, fail_host="//b/")
        _, out = run(["--account", AUTH.replace(".prd.", ".non."), "http://a", "http://b"], api)
        self.assertIn("DIFF", out)
        self.assertNotIn("     A:", out)

    def test_csv_records_every_request_but_no_credential(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lat.csv"
            run(PRD_SINGLE + ["--interval", "10", "--max-cycles", "2", "--csv", str(path)])
            rows = path.read_text().splitlines()
        self.assertEqual(rows[0], "utc,cycle,backend,op,method,status,latency_ms")
        self.assertEqual(len(rows) - 1, 2 * 21)  # per cycle: 18 profile ops, 2 logins, 1 preflight
        self.assertNotIn(PASSWORD, "\n".join(rows))
        self.assertNotIn(EMAIL, "\n".join(rows))

    def test_accepted_after_delete_difference_passes(self):
        for status in (403, 404):
            code, _ = run(PRD_SINGLE, FakeApi(fail={"GET by-id after delete": status}))
            self.assertEqual(code, 0)


class LoopTest(unittest.TestCase):
    def test_stops_at_max_cycles_on_a_fixed_rate(self):
        api, clock = FakeApi(), Clock()
        code, out = run(PRD_SINGLE + ["--interval", "10", "--max-cycles", "4"], api, clock=clock)
        self.assertEqual(code, 0)
        self.assertIn("stopped: max cycles (4); 4 cycle(s), 0 failed", out)
        self.assertEqual(clock.sleeps, [10, 10, 10])

    def test_stops_after_consecutive_failures(self):
        api = FakeApi(fail={"GET all (non-admin)": 500})
        code, out = run(PRD_SINGLE + ["--interval", "10", "--max-cycles", "50", "--max-consecutive-failures", "3"], api)
        self.assertEqual(code, 1)
        self.assertIn("stopped: 3 consecutive failed cycle(s); 3 cycle(s), 3 failed", out)

    def test_duration_bounds_the_run_before_the_cycle_cap(self):
        _, out = run(PRD_SINGLE + ["--interval", "10", "--max-cycles", "100", "--duration", "35"])
        self.assertIn("stopped: duration/deadline reached; 4 cycle(s)", out)

    def test_presets_match_the_load_plan(self):
        a = cp.parse_args(["--preset", "baseline"] + PRD_SINGLE)
        self.assertEqual((a.interval, a.duration, a.max_cycles), (10.0, 3600.0, 360))
        a = cp.parse_args(["--preset", "soak", "--max-cycles", "5"] + PRD_SINGLE)
        self.assertEqual((a.interval, a.max_cycles), (300.0, 5))


class GuardrailTest(unittest.TestCase):
    def assertRejected(self, argv):
        with self.assertRaises(SystemExit), unittest.mock.patch("sys.stderr", io.StringIO()):
            cp.parse_args(argv)

    def test_prd_without_account_is_rejected(self):
        self.assertRejected(["--env", "prd", "--single", AUTH, BASE])
        self.assertRejected(["--single", AUTH, BASE])

    def test_prd_urls_need_prd_env(self):
        self.assertRejected(["--account", "--single", AUTH, BASE])

    def test_caps_are_enforced(self):
        self.assertRejected(PRD_SINGLE + ["--interval", "10", "--max-cycles", str(cp.MAX_CYCLES_CEILING + 1)])
        self.assertRejected(PRD_SINGLE + ["--max-cycles", "5"])
        self.assertRejected(PRD_SINGLE + ["--max-consecutive-failures", "0"])

    def test_default_mode_is_unchanged(self):
        a = cp.parse_args(["https://usersrole.non.jdwlabs.com", "http://a", "http://b"])
        self.assertEqual((a.account, a.single, a.env, a.max_cycles), (False, False, "non", 1))


class DryRunTest(unittest.TestCase):
    def test_prints_sequence_without_sending_or_reading_credentials(self):
        api = FakeApi()
        code, out = run(["--dry-run", "--preset", "baseline"] + PRD_SINGLE, api, environ=ENV)
        self.assertEqual(code, 0)
        self.assertEqual(api.calls, [])
        self.assertNotIn(PASSWORD, out)
        self.assertNotIn(EMAIL, out)
        requests = [l for l in out.splitlines() if l.startswith("  ")]
        self.assertEqual(len(requests), 21)  # 18 profile ops, 2 logins, 1 preflight
        self.assertIn("up to 360 cycle(s), every 10s", out)

    def test_dry_run_of_default_mode_shows_registration(self):
        code, out = run(["--dry-run", "https://usersrole.non.jdwlabs.com", "http://a", "http://b"], environ={})
        self.assertEqual(code, 0)
        self.assertIn("/auth/user", out)


if __name__ == "__main__":
    unittest.main()
