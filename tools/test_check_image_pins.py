#!/usr/bin/env python3
"""Unit tests for tools/check-image-pins.py.

Each test below pins one behaviour the check previously got wrong — a crash, a
false negative that let a mutable reference through, or an exemption that
outlived the reference it was written for. Run with:

    python3 -m unittest discover -s tools -p 'test_*.py'
"""
import importlib.util
import textwrap
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "check-image-pins.py"
_spec = importlib.util.spec_from_file_location("check_image_pins", MODULE_PATH)
cip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cip)

PINNED_TAG = "1.0.0@sha256:" + "a" * 64
OTHER_DIGEST = "@sha256:" + "b" * 64

BASE_VALUES = textwrap.dedent(
    """\
    image:
      repository: jdwlabs/app
      pullPolicy: IfNotPresent
      tag: ""
    """
)


class RepoFixture:
    """Builds a throwaway repo tree with the same shape check() expects."""

    def __init__(self, root: Path):
        self.root = root
        (root / "charts").mkdir(parents=True, exist_ok=True)

    def chart(self, name: str, base: str = BASE_VALUES, library: bool = False) -> "RepoFixture":
        chart_dir = self.root / "charts" / name
        chart_dir.mkdir(parents=True, exist_ok=True)
        kind = "type: library\n" if library else ""
        (chart_dir / "Chart.yaml").write_text(f"name: {name}\nversion: 0.1.0\n{kind}", encoding="utf-8")
        if base is not None:
            (chart_dir / "values.yaml").write_text(base, encoding="utf-8")
        return self

    def overlay(self, chart: str, env: str, body: str) -> "RepoFixture":
        (self.root / "charts" / chart / f"values-{env}.yaml").write_text(
            textwrap.dedent(body), encoding="utf-8"
        )
        return self

    def template(self, chart: str, name: str, body: str) -> "RepoFixture":
        directory = self.root / "charts" / chart / "templates"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(textwrap.dedent(body), encoding="utf-8")
        return self

    def allowlist(self, body: str) -> "RepoFixture":
        (self.root / "tools").mkdir(parents=True, exist_ok=True)
        (self.root / cip.ALLOWLIST_RELPATH).write_text(textwrap.dedent(body), encoding="utf-8")
        return self

    def check(self) -> cip.Report:
        return cip.check(self.root)


class CheckImagePinsTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = RepoFixture(Path(self._tmp.name))

    def rendered(self, report):
        return [r.rendered for r in report.violations]

    # --- baseline -------------------------------------------------------

    def test_digest_pinned_tag_passes(self):
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        report = self.repo.check()
        self.assertTrue(report.ok, cip.render(report))
        self.assertEqual(1, len(report.pinned))

    def test_report_renders_a_definitive_success_line(self):
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        self.assertIn("0 issues", cip.render(self.repo.check()))

    def test_library_chart_needs_no_overlay(self):
        self.repo.chart("common", base=None, library=True)
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        self.assertTrue(self.repo.check().ok)

    # --- defect 1: unquoted numeric tag crashed the check ---------------

    def test_unquoted_float_tag_does_not_crash(self):
        self.repo.chart("app").overlay("app", "prd", "image:\n  tag: 1.0\n")
        report = self.repo.check()  # previously: TypeError on re.search
        self.assertFalse(report.ok)
        self.assertEqual(1, len(report.malformed))
        self.assertIn("unquoted YAML number", report.malformed[0].problem)

    def test_unquoted_int_tag_does_not_crash(self):
        self.repo.chart("app").overlay("app", "prd", "image:\n  tag: 3\n")
        report = self.repo.check()
        self.assertFalse(report.ok)
        self.assertEqual(1, len(report.malformed))

    def test_unquoted_numeric_tag_is_not_allowlistable(self):
        self.repo.chart("app").overlay("app", "prd", "image:\n  tag: 1.0\n")
        self.repo.allowlist(
            """\
            exceptions:
              - path: charts/app/values-prd.yaml
                repository: jdwlabs/app
                tag: "1.0"
                reason: trying to excuse a malformed reference
            """
        )
        report = self.repo.check()
        self.assertFalse(report.ok)
        self.assertEqual(1, len(report.malformed))
        self.assertEqual([], report.allowed)

    # --- defect 2: environments were hard-coded to non/prd --------------

    def test_unlisted_environment_overlay_is_checked(self):
        self.repo.chart("app").overlay("app", "dev", 'image:\n  tag: "latest"\n')
        report = self.repo.check()  # previously: silently skipped, exit 0
        self.assertFalse(report.ok)
        self.assertEqual(["jdwlabs/app:latest"], self.rendered(report))

    def test_every_overlay_is_checked_independently(self):
        self.repo.chart("app")
        self.repo.overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        self.repo.overlay("app", "dev", 'image:\n  tag: "latest"\n')
        self.repo.overlay("app", "qa", 'image:\n  tag: "nightly"\n')
        report = self.repo.check()
        self.assertEqual(
            {"charts/app/values-dev.yaml", "charts/app/values-qa.yaml"},
            {r.path for r in report.violations},
        )

    def test_chart_with_no_overlay_is_reported_not_skipped(self):
        self.repo.chart("app")
        report = self.repo.check()
        self.assertFalse(report.ok)
        self.assertEqual(["charts/app"], report.unchecked_charts)

    # --- defect 3: only the top-level image key was read ----------------

    def test_sidecar_image_map_is_checked(self):
        self.repo.chart("app").overlay(
            "app",
            "prd",
            f"""\
            image:
              tag: "{PINNED_TAG}"
            sidecar:
              image:
                repository: jdwlabs/sidecar
                tag: latest
            """,
        )
        report = self.repo.check()  # previously: invisible
        self.assertFalse(report.ok)
        self.assertEqual(["jdwlabs/sidecar:latest"], self.rendered(report))
        self.assertEqual("sidecar.image", report.violations[0].where)

    def test_raw_image_string_is_checked(self):
        self.repo.chart("app").overlay(
            "app",
            "prd",
            f"""\
            image:
              tag: "{PINNED_TAG}"
            initContainer:
              image: busybox:latest
            """,
        )
        report = self.repo.check()  # previously: invisible
        self.assertFalse(report.ok)
        self.assertEqual(["busybox:latest"], self.rendered(report))

    def test_image_reference_inside_a_list_is_checked(self):
        self.repo.chart("app").overlay(
            "app",
            "prd",
            f"""\
            image:
              tag: "{PINNED_TAG}"
            extraContainers:
              - name: proxy
                image: envoyproxy/envoy:v1.29
            """,
        )
        report = self.repo.check()
        self.assertEqual(["envoyproxy/envoy:v1.29"], self.rendered(report))

    def test_nested_image_map_without_a_tag_key_is_checked(self):
        self.repo.chart("app").overlay(
            "app",
            "prd",
            f"""\
            image:
              tag: "{PINNED_TAG}"
            sidecar:
              image:
                repository: jdwlabs/sidecar
            """,
        )
        report = self.repo.check()
        self.assertFalse(report.ok)
        self.assertEqual(1, len(report.violations))

    def test_pinned_nested_reference_passes(self):
        self.repo.chart("app").overlay(
            "app",
            "prd",
            f"""\
            image:
              tag: "{PINNED_TAG}"
            sidecar:
              image: "busybox:1.37{OTHER_DIGEST}"
            """,
        )
        self.assertTrue(self.repo.check().ok)

    def test_registry_port_is_not_mistaken_for_a_tag(self):
        self.repo.chart("app").overlay(
            "app",
            "prd",
            f"""\
            image:
              tag: "{PINNED_TAG}"
            sidecar:
              image: registry.local:5000/team/tool:v2
            """,
        )
        report = self.repo.check()
        self.assertEqual("registry.local:5000/team/tool", report.violations[0].repository)
        self.assertEqual("v2", report.violations[0].tag)

    # --- defect 4: digest-only tag passed but renders unpullable --------

    def test_digest_only_tag_is_rejected(self):
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{OTHER_DIGEST}"\n')
        report = self.repo.check()  # previously: counted as digest-pinned
        self.assertFalse(report.ok)
        self.assertEqual([], report.pinned)
        self.assertEqual(1, len(report.malformed))
        self.assertIn("digest-only tag", report.malformed[0].problem)

    def test_digest_only_tag_is_not_allowlistable(self):
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{OTHER_DIGEST}"\n')
        self.repo.allowlist(
            f"""\
            exceptions:
              - path: charts/app/values-prd.yaml
                repository: jdwlabs/app
                tag: "{OTHER_DIGEST}"
                reason: trying to excuse an unpullable reference
            """
        )
        report = self.repo.check()
        self.assertFalse(report.ok)
        self.assertEqual([], report.allowed)

    def test_truncated_digest_is_rejected(self):
        self.repo.chart("app").overlay("app", "prd", 'image:\n  tag: "1.0.0@sha256:abc123"\n')
        report = self.repo.check()
        self.assertFalse(report.ok)
        self.assertEqual(1, len(report.malformed))
        self.assertIn("digest component", report.malformed[0].problem)

    def test_tag_starting_with_a_dot_is_rejected(self):
        self.repo.chart("app").overlay("app", "prd", 'image:\n  tag: ".1.0.0"\n')
        report = self.repo.check()
        self.assertEqual(1, len(report.malformed))
        self.assertIn("not a valid OCI tag", report.malformed[0].problem)

    # --- defect 5: allowlist key was path-only --------------------------

    ALLOWLIST_FOR_EMPTY_TAG = """\
        exceptions:
          - path: charts/app/values-non.yaml
            repository: jdwlabs/app
            tag: ""
            reason: non deliberately tracks Chart.appVersion under pullPolicy Always
        """

    def test_allowlist_entry_covers_the_reference_it_describes(self):
        self.repo.chart("app").overlay("app", "non", "image:\n  pullPolicy: Always\n")
        self.repo.allowlist(self.ALLOWLIST_FOR_EMPTY_TAG)
        report = self.repo.check()
        self.assertTrue(report.ok, cip.render(report))
        self.assertEqual(1, len(report.allowed))

    def test_allowlist_entry_does_not_cover_a_changed_repository(self):
        self.repo.chart("app").overlay(
            "app", "non", "image:\n  repository: attacker/other\n  pullPolicy: Always\n"
        )
        self.repo.allowlist(self.ALLOWLIST_FOR_EMPTY_TAG)
        report = self.repo.check()  # previously: exempt, exit 0
        self.assertFalse(report.ok)
        self.assertEqual(["attacker/other (no tag — falls back to Chart.appVersion)"], self.rendered(report))

    def test_allowlist_entry_does_not_cover_a_changed_tag(self):
        self.repo.chart("app").overlay("app", "non", 'image:\n  tag: "latest"\n')
        self.repo.allowlist(self.ALLOWLIST_FOR_EMPTY_TAG)
        report = self.repo.check()  # previously: exempt, exit 0
        self.assertFalse(report.ok)
        self.assertEqual(["jdwlabs/app:latest"], self.rendered(report))

    def test_allowlist_entry_does_not_leak_to_another_environment(self):
        self.repo.chart("app")
        self.repo.overlay("app", "non", "image:\n  pullPolicy: Always\n")
        self.repo.overlay("app", "prd", 'image:\n  tag: ""\n')
        self.repo.allowlist(self.ALLOWLIST_FOR_EMPTY_TAG)
        report = self.repo.check()
        self.assertEqual(["charts/app/values-prd.yaml"], [r.path for r in report.violations])

    def test_unused_allowlist_entry_is_stale(self):
        self.repo.chart("app").overlay("app", "non", f'image:\n  tag: "{PINNED_TAG}"\n')
        self.repo.allowlist(self.ALLOWLIST_FOR_EMPTY_TAG)
        report = self.repo.check()
        self.assertFalse(report.ok)
        self.assertEqual([("charts/app/values-non.yaml", "jdwlabs/app", "")], report.stale)

    def test_allowlist_entry_without_a_repository_is_rejected(self):
        self.repo.chart("app").overlay("app", "non", "image:\n  pullPolicy: Always\n")
        self.repo.allowlist(
            """\
            exceptions:
              - path: charts/app/values-non.yaml
                reason: path-only key, the shape this check no longer accepts
            """
        )
        with self.assertRaises(SystemExit):
            self.repo.check()

    def test_allowlist_entry_without_a_reason_is_rejected(self):
        self.repo.chart("app").overlay("app", "non", "image:\n  pullPolicy: Always\n")
        self.repo.allowlist(
            """\
            exceptions:
              - path: charts/app/values-non.yaml
                repository: jdwlabs/app
                tag: ""
                reason: "   "
            """
        )
        with self.assertRaises(SystemExit):
            self.repo.check()

    def test_allowlist_entry_with_an_unquoted_numeric_tag_is_rejected(self):
        self.repo.chart("app").overlay("app", "non", "image:\n  tag: 1.0\n")
        self.repo.allowlist(
            """\
            exceptions:
              - path: charts/app/values-non.yaml
                repository: jdwlabs/app
                tag: 1.0
                reason: an unquoted tag here would never key against anything
            """
        )
        with self.assertRaises(SystemExit):
            self.repo.check()

    def test_duplicate_allowlist_entries_are_rejected(self):
        self.repo.chart("app").overlay("app", "non", "image:\n  pullPolicy: Always\n")
        self.repo.allowlist(
            """\
            exceptions:
              - path: charts/app/values-non.yaml
                repository: jdwlabs/app
                tag: ""
                reason: first entry
              - path: charts/app/values-non.yaml
                repository: jdwlabs/app
                tag: ""
                reason: second entry for the same reference
            """
        )
        with self.assertRaises(SystemExit):
            self.repo.check()

    # --- chart templates ------------------------------------------------

    def test_literal_template_image_is_checked(self):
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        self.repo.template("app", "job.yaml", "spec:\n  containers:\n    - image: busybox\n")
        report = self.repo.check()
        self.assertFalse(report.ok)
        self.assertEqual(["busybox (no tag — implicit :latest)"], self.rendered(report))
        self.assertEqual("charts/app/templates/job.yaml:3", report.violations[0].location)

    def test_templated_image_line_is_left_to_the_values_scan(self):
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        self.repo.template(
            "app",
            "deployment.yaml",
            'spec:\n  containers:\n    - image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"\n',
        )
        self.assertTrue(self.repo.check().ok)

    def test_image_pull_policy_line_is_not_an_image_reference(self):
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        self.repo.template("app", "deployment.yaml", "spec:\n  imagePullPolicy: Always\n")
        self.assertTrue(self.repo.check().ok)

    def test_library_chart_templates_are_still_scanned(self):
        self.repo.chart("common", base=None, library=True)
        self.repo.template("common", "_test.yaml", "spec:\n  containers:\n    - image: busybox\n")
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        report = self.repo.check()
        self.assertEqual(["charts/common/templates/_test.yaml"], [r.path for r in report.violations])

    def test_commented_template_image_value_is_trimmed(self):
        self.repo.chart("app").overlay("app", "prd", f'image:\n  tag: "{PINNED_TAG}"\n')
        self.repo.template("app", "job.yaml", "spec:\n  containers:\n    - image: busybox:1.37  # wget\n")
        report = self.repo.check()
        self.assertEqual("busybox", report.violations[0].repository)
        self.assertEqual("1.37", report.violations[0].tag)

    # --- values merge semantics -----------------------------------------

    def test_overlay_inherits_the_base_repository(self):
        self.repo.chart("app").overlay("app", "prd", 'image:\n  tag: "latest"\n')
        report = self.repo.check()
        self.assertEqual("jdwlabs/app", report.violations[0].repository)

    def test_overlay_that_never_mentions_image_still_yields_a_reference(self):
        self.repo.chart("app").overlay("app", "prd", "replicaCount: 2\n")
        report = self.repo.check()
        self.assertEqual(1, len(report.violations))
        self.assertEqual("charts/app/values-prd.yaml", report.violations[0].path)

    def test_overlay_null_tag_falls_back_like_helm(self):
        self.repo.chart("app", base=BASE_VALUES.replace('tag: ""', 'tag: "1.2.3"'))
        self.repo.overlay("app", "prd", "image:\n  tag: null\n")
        report = self.repo.check()
        self.assertEqual("", report.violations[0].tag)


if __name__ == "__main__":
    unittest.main()
