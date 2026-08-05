#!/usr/bin/env python3
"""Unit tests for tools/check-prd-drift.py.

Each test below pins one way this check could report a pass over a prd that is
actually behind — a chart it never graded, a digest suffix compared against a
plain version, a hold that outlived what it excused, or an age it could not
measure and therefore treated as no drift at all. Run with:

    python3 -m unittest discover -s tools -p 'test_*.py'
"""

import importlib.util
import json
import subprocess
import tempfile
import textwrap
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "check-prd-drift.py"
_spec = importlib.util.spec_from_file_location("check_prd_drift", MODULE_PATH)
cpd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpd)

DIGEST = "@sha256:" + "a" * 64
OTHER_DIGEST = "@sha256:" + "b" * 64


def hours_ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


class RepoFixture:
    """Builds a throwaway repo tree with the same shape check() expects, plus a
    stub history so age can be asserted without a git repository."""

    def __init__(self, root: Path):
        self.root = root
        (root / "charts").mkdir(parents=True, exist_ok=True)
        (root / "tools").mkdir(parents=True, exist_ok=True)
        self.histories: dict[str, list[tuple[datetime, str | None]]] = {}
        self.holds("holds: []\n")

    def chart(self, name: str, app_version: str | None = "1.0.0", library: bool = False) -> "RepoFixture":
        chart_dir = self.root / "charts" / name
        chart_dir.mkdir(parents=True, exist_ok=True)
        kind = "type: library\n" if library else ""
        app = f'appVersion: "{app_version}"\n' if app_version is not None else ""
        (chart_dir / "Chart.yaml").write_text(f"name: {name}\nversion: 0.1.0\n{kind}{app}", encoding="utf-8")
        return self

    def prd(self, chart: str, body: str) -> "RepoFixture":
        (self.root / "charts" / chart / "values-prd.yaml").write_text(
            textwrap.dedent(body), encoding="utf-8"
        )
        return self

    def pin(self, chart: str, tag: str) -> "RepoFixture":
        return self.prd(chart, f'image:\n  tag: "{tag}"\n')

    def history(self, chart: str, entries: list[tuple[float, str | None]]) -> "RepoFixture":
        """Newest first, as (hours ago, appVersion) pairs."""
        self.histories[chart] = [(hours_ago(h), v) for h, v in entries]
        return self

    def holds(self, body: str) -> "RepoFixture":
        (self.root / "tools" / "prd-drift-holds.yaml").write_text(
            textwrap.dedent(body), encoding="utf-8"
        )
        return self

    def check(self) -> cpd.Report:
        return cpd.check(self.root, history_reader=lambda _root, name: self.histories.get(name, []))

    def names(self, charts) -> list[str]:
        return [c.name for c in charts]


class CheckPrdDriftTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = RepoFixture(Path(self._tmp.name))

    # --- baseline -------------------------------------------------------

    def test_matching_versions_are_level(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.0" + DIGEST)
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.level))
        self.assertTrue(report.ok, cpd.render(report))

    def test_report_renders_a_definitive_success_line(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.0" + DIGEST)
        self.assertIn("0 issues", cpd.render(self.repo.check()))

    def test_summary_line_counts_every_bucket(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.0" + DIGEST)
        rendered = cpd.render(self.repo.check())
        self.assertIn("1 charts graded, 1 level, 0 drifted", rendered)

    # --- the failure this exists to catch --------------------------------

    def test_old_drift_is_a_finding(self):
        self.repo.chart("app", "1.0.3").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(96, "1.0.3"), (100, "1.0.1"), (200, "1.0.0")])
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.drifted))
        self.assertFalse(report.ok)

    def test_drift_report_names_both_versions_and_the_age(self):
        self.repo.chart("app", "1.0.3").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(96, "1.0.3"), (100, "1.0.1"), (200, "1.0.0")])
        rendered = cpd.render(self.repo.check())
        self.assertIn("1.0.3", rendered)
        self.assertIn("1.0.0", rendered)
        self.assertIn("4d", rendered)

    def test_recent_drift_is_settling_not_a_finding(self):
        self.repo.chart("app", "1.0.1").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(2, "1.0.1"), (30, "1.0.0")])
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.settling))
        self.assertTrue(report.ok, cpd.render(report))

    def test_drift_age_is_measured_from_when_the_gap_opened(self):
        # The gap opened when appVersion first left 1.0.0, not at the newest bump.
        self.repo.chart("app", "1.0.3").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(2, "1.0.3"), (50, "1.0.1"), (200, "1.0.0")])
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.drifted))
        self.assertAlmostEqual(50, report.drifted[0].age_hours, delta=1)

    def test_gap_older_than_the_history_window_reports_a_lower_bound(self):
        self.repo.chart("app", "2.0.0").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(2, "2.0.0"), (500, "1.9.0")])  # previously: age unknown, read as no drift
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.drifted))
        self.assertTrue(report.drifted[0].at_least)
        self.assertIn(">=", report.drifted[0].age)

    def test_drift_with_no_history_at_all_is_still_a_finding(self):
        self.repo.chart("app", "1.0.3").pin("app", "1.0.0" + DIGEST)
        report = self.repo.check()  # previously: age None compared as recent, so it passed
        self.assertEqual(["app"], self.repo.names(report.drifted))
        self.assertFalse(report.ok)

    # --- tag parsing -----------------------------------------------------

    def test_digest_suffix_is_stripped_before_comparing(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.0" + DIGEST)
        self.assertEqual(["app"], self.repo.names(self.repo.check().level))

    def test_a_different_digest_on_the_same_version_is_not_drift(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.0" + OTHER_DIGEST)
        self.assertTrue(self.repo.check().ok)

    def test_tag_is_read_as_yaml_not_by_line_position(self):
        self.repo.chart("app", "1.0.0").prd("app", """\
            revisionHistoryLimit: 1
            image:
              # A comment block sits between image: and tag: in a real chart.
              tag: "1.0.0%s"
            """ % DIGEST)
        self.assertEqual(["app"], self.repo.names(self.repo.check().level))

    def test_unpinned_tag_still_compares(self):
        self.repo.chart("app", "1.0.1").pin("app", "1.0.0")
        self.repo.history("app", [(96, "1.0.1"), (200, "1.0.0")])
        self.assertEqual(["app"], self.repo.names(self.repo.check().drifted))

    def test_stray_at_sign_is_not_treated_as_a_pin(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.0@nonsense")
        report = self.repo.check()  # previously: truncated to 1.0.0 and graded level
        self.assertEqual(["app"], self.repo.names(report.unreadable))

    # --- surfaces that must not read as a pass ---------------------------

    def test_chart_without_a_prd_overlay_is_unreadable(self):
        self.repo.chart("app", "1.0.0")
        report = self.repo.check()  # previously: contributed nothing, exit 0
        self.assertEqual(["app"], self.repo.names(report.unreadable))
        self.assertFalse(report.ok)

    def test_chart_without_an_appversion_is_unreadable(self):
        self.repo.chart("app", None).pin("app", "1.0.0" + DIGEST)
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.unreadable))
        self.assertFalse(report.ok)

    def test_empty_prd_tag_is_unreadable(self):
        self.repo.chart("app", "1.0.0").prd("app", 'image:\n  tag: ""\n')
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.unreadable))
        self.assertIn("falls back to Chart.appVersion", report.unreadable[0].problem)

    def test_unorderable_versions_are_unreadable_not_guessed(self):
        self.repo.chart("app", "main").pin("app", "1.0.0" + DIGEST)
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.unreadable))
        self.assertFalse(report.ok)

    def test_library_chart_is_not_graded(self):
        self.repo.chart("common", app_version=None, library=True)
        report = self.repo.check()
        self.assertEqual(0, report.graded)
        self.assertTrue(report.ok, cpd.render(report))

    def test_prd_ahead_of_appversion_is_a_finding(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.4" + DIGEST)
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.ahead))
        self.assertFalse(report.ok)

    def test_major_gap_is_ordered_numerically_not_lexically(self):
        # "0.10.14" sorts after "1.0.5" as a string; prd is behind, not ahead.
        self.repo.chart("app", "1.0.5").pin("app", "0.10.14" + DIGEST)
        self.repo.history("app", [(96, "1.0.5")])
        report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.drifted))
        self.assertEqual([], report.ahead)

    def test_every_chart_is_graded_not_just_the_first(self):
        self.repo.chart("a", "1.0.0").pin("a", "1.0.0" + DIGEST)
        self.repo.chart("b", "2.0.1").pin("b", "2.0.0" + DIGEST)
        self.repo.history("b", [(96, "2.0.1"), (200, "2.0.0")])
        report = self.repo.check()
        self.assertEqual(2, report.graded)
        self.assertEqual(["b"], self.repo.names(report.drifted))

    # --- holds -----------------------------------------------------------

    def test_hold_suppresses_a_known_gap(self):
        self.repo.chart("app", "1.0.5").pin("app", "0.10.14" + DIGEST)
        self.repo.history("app", [(500, "1.0.5")])
        self.repo.holds("""\
            holds:
              - chart: app
                prdTag: "0.10.14"
                reason: cross-generation verification outstanding
            """)
        report = self.repo.check()
        self.assertEqual(["app"], [c.name for c, _ in report.held])
        self.assertTrue(report.ok, cpd.render(report))

    def test_hold_stops_applying_once_prd_moves(self):
        self.repo.chart("app", "1.0.5").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(500, "1.0.5")])
        self.repo.holds("""\
            holds:
              - chart: app
                prdTag: "0.10.14"
                reason: cross-generation verification outstanding
            """)
        report = self.repo.check()  # previously: exemption outlived the position it excused
        self.assertEqual(["app"], self.repo.names(report.drifted))
        self.assertEqual([("app", "0.10.14")], report.stale_holds)
        self.assertFalse(report.ok)

    def test_hold_does_not_leak_to_another_chart(self):
        self.repo.chart("a", "1.0.5").pin("a", "0.10.14" + DIGEST)
        self.repo.chart("b", "1.0.5").pin("b", "0.10.14" + DIGEST)
        self.repo.history("a", [(500, "1.0.5")])
        self.repo.history("b", [(500, "1.0.5")])
        self.repo.holds("""\
            holds:
              - chart: a
                prdTag: "0.10.14"
                reason: cross-generation verification outstanding
            """)
        report = self.repo.check()
        self.assertEqual(["b"], self.repo.names(report.drifted))

    def test_hold_without_a_reason_is_fatal(self):
        self.repo.chart("app", "1.0.5").pin("app", "0.10.14" + DIGEST)
        self.repo.holds("""\
            holds:
              - chart: app
                prdTag: "0.10.14"
            """)
        with self.assertRaises(SystemExit):
            self.repo.check()

    def test_hold_on_a_level_chart_is_stale(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.0" + DIGEST)
        self.repo.holds("""\
            holds:
              - chart: app
                prdTag: "1.0.0"
                reason: no longer needed
            """)
        report = self.repo.check()
        self.assertEqual([("app", "1.0.0")], report.stale_holds)
        self.assertFalse(report.ok)

    # --- threshold and output --------------------------------------------

    def test_threshold_is_configurable(self):
        self.repo.chart("app", "1.0.1").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(30, "1.0.1"), (200, "1.0.0")])
        self.assertEqual(["app"], self.repo.names(self.repo.check().drifted))

        with unittest.mock.patch.dict("os.environ", {"PRD_DRIFT_MAX_AGE_HOURS": "72"}):
            report = self.repo.check()
        self.assertEqual(["app"], self.repo.names(report.settling))
        self.assertTrue(report.ok, cpd.render(report))

    def test_full_output_lists_level_charts(self):
        self.repo.chart("app", "1.0.0").pin("app", "1.0.0" + DIGEST)
        report = self.repo.check()
        self.assertNotIn("Level —", cpd.render(report))
        self.assertIn("Level —", cpd.render(report, full=True))

    def test_json_output_is_machine_readable(self):
        self.repo.chart("app", "1.0.3").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(96, "1.0.3"), (200, "1.0.0")])
        payload = json.loads(cpd.as_json(self.repo.check()))
        self.assertFalse(payload["ok"])
        self.assertEqual(1, payload["counts"]["drifted"])
        self.assertEqual("drifted", payload["charts"][0]["state"])
        self.assertEqual("1.0.0", payload["charts"][0]["prdVersion"])

    def test_table_aligns_and_names_its_columns(self):
        self.repo.chart("app", "1.0.3").pin("app", "1.0.0" + DIGEST)
        self.repo.history("app", [(96, "1.0.3"), (200, "1.0.0")])
        rendered = cpd.render(self.repo.check())
        self.assertIn("chart", rendered)
        self.assertIn("appVersion", rendered)
        self.assertIn("behind for", rendered)


class GitHistoryReaderTest(unittest.TestCase):
    """The age half of the report depends on reading appVersion out of real git
    history, which the stub above deliberately does not exercise."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.chart_dir = self.root / "charts" / "app"
        self.chart_dir.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def commit(self, app_version: str):
        (self.chart_dir / "Chart.yaml").write_text(
            f'name: app\nversion: 0.1.0\nappVersion: "{app_version}"\n', encoding="utf-8"
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"bump to {app_version}")

    def test_history_is_newest_first_with_each_appversion(self):
        self.commit("1.0.0")
        self.commit("1.0.1")
        self.commit("1.0.2")
        history = cpd.git_appversion_history(self.root, "app")
        self.assertEqual(["1.0.2", "1.0.1", "1.0.0"], [version for _, version in history])

    def test_measure_drift_finds_the_commit_that_opened_the_gap(self):
        self.commit("1.0.0")
        self.commit("1.0.1")
        self.commit("1.0.2")
        history = cpd.git_appversion_history(self.root, "app")
        since, at_least = cpd.measure_drift(history, "1.0.0")
        self.assertFalse(at_least)
        self.assertEqual(history[1][0], since)  # the 1.0.1 commit, not the 1.0.2 one

    def test_measure_drift_reports_a_bound_when_prd_predates_the_window(self):
        self.commit("2.0.0")
        self.commit("2.0.1")
        history = cpd.git_appversion_history(self.root, "app")
        since, at_least = cpd.measure_drift(history, "1.0.0")
        self.assertTrue(at_least)
        self.assertEqual(history[-1][0], since)

    def test_level_history_reports_no_drift(self):
        self.commit("1.0.0")
        history = cpd.git_appversion_history(self.root, "app")
        self.assertEqual((None, False), cpd.measure_drift(history, "1.0.0"))


if __name__ == "__main__":
    unittest.main()
