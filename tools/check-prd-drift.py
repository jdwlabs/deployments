#!/usr/bin/env python3
"""Report charts whose released appVersion never reached values-prd.yaml.

The release pipeline in the apps repo bumps `Chart.yaml` `appVersion` here by
direct bot commit, which moves non. Nothing moves prd: `values-prd.yaml`
`image.tag` is written only by the promotion workflow, and that workflow's
automatic trigger is dormant while E2E is manual-only. So the two files drift
apart silently, and every signal on the way stays green while they do — the
release job succeeds, each deliver job succeeds, the chart-bump PR self-merges.
Nothing in that chain has an opinion about prd, because nothing in that chain
touches prd.

That is not a miswiring to fix here; the dormant trigger is deliberate and
documented. The gap is that the resulting distance is invisible. It was found
once by reading `values-prd.yaml` by hand during an incident, at which point
prd had been three container versions behind for four days.

Scope — every non-library chart under charts/ is graded, and a chart that
yields no answer is a failure rather than a silent pass:

  * `Chart.yaml` `appVersion` is the latest release delivered to non, and the
    only local record of what the apps pipeline last published. Comparing
    against it rather than against a registry keeps this check honest about
    one thing only: whether what was released reached prd.
  * `values-prd.yaml` `image.tag` is parsed as YAML, not by line position. The
    tag is not always the line after `image:` — a comment block sits between
    them in at least one chart — and `image:` is not always the first key.
  * the digest suffix is stripped before comparing. A pinned tag is
    `<version>@sha256:<64 hex>`; the version in front of the `@` is what
    corresponds to `appVersion`, and the digest deliberately has no counterpart
    in `Chart.yaml`.
  * a chart with no `values-prd.yaml`, no `appVersion`, or a tag carrying no
    recognisable version is reported as unreadable. Grading it as level would
    turn every one of those into a pass.

How long the two have differed is the finding, not decoration. Drift measured
in hours is a promotion in flight; drift measured in days is the failure this
exists to catch. The age is read from git history — walking `Chart.yaml`
backwards to the last commit whose `appVersion` still equalled the tag prd
runs — so a run reports how long the gap has stood, not merely that it exists
right now. Drift younger than the threshold is reported and tolerated; older
than it is a finding. A history walk that never reaches a level commit reports
a lower bound rather than an unknown, because "at least this long" is a
finding and "unknown" reads as a pass.

A deliberate hold is declared in tools/prd-drift-holds.yaml with a reason, and
is keyed on (chart, prd tag). A cross-generation gap awaiting verification is
a real state this repo has been in for weeks at a time, and a check that
cannot express it goes red forever and teaches its reader to skip it. Keying
on the pinned tag means the hold stops applying the moment prd moves, so an
exception cannot outlive the position it was written to excuse. A hold that no
longer matches anything is reported and fails, for the same reason.

IMPORTANT — this compares two files in this repository and never contacts a
registry or the GitHub API. That is what makes it trustworthy as the detector
for this particular failure: a check reading workflow run conclusions would
have to decide what `cancelled` means, and promotion runs do get cancelled in
normal operation because they share one serialised concurrency group and
GitHub keeps only one pending run per group. A `cancelled` conclusion reads as
success to anything matching only on `failure`. Repository state has no such
ambiguity — either the version prd runs is the one that was released, or it is
not.

Usage:
    python3 tools/check-prd-drift.py [--max-age-hours N] [--full] [--json]

Exit codes: 0 = no chart has been behind longer than the threshold; 1 = one or
more charts drifted past it, a prd pin ahead of `appVersion`, a chart that
could not be read, or a hold matching nothing.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

HOLDS_RELPATH = "tools/prd-drift-holds.yaml"

# Exit codes. 0 and 1 are the verdicts — prd is level, or prd is behind. 2 says
# no verdict was reached at all, so a caller never has to read "the check
# failed" as "the check found something".
EXIT_LEVEL = 0
EXIT_DRIFTED = 1
EXIT_NO_VERDICT = 2

# Drift younger than this is a promotion in flight rather than a finding. A day
# is long enough that an ordinary same-day promotion never reports, and short
# enough that a gap surviving one scheduled run is already visible.
DEFAULT_MAX_AGE_HOURS = 24

# Bounds the history walk. A chart whose prd pin predates this many commits
# reports a lower-bound age instead of scanning its whole history on every run.
HISTORY_LIMIT = 200

# Strips the pin from "<version>@sha256:<64 lowercase hex>". Only a full digest
# is removed: a tag containing a stray "@" is not a pin and must not be
# silently truncated into one.
DIGEST_SUFFIX_RE = re.compile(r"@sha256:[0-9a-f]{64}$")

# A version this check can order. Trailing pre-release/build metadata is kept
# for display and equality but ignored for ordering, which only ever has to
# answer "is prd behind or ahead".
VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[-+].*)?$")


@dataclass
class Chart:
    """One chart's two versions and the distance between them. `prd_tag` is the
    raw value an author would edit; `prd_version` is that value with its digest
    pin removed, which is the half that corresponds to `appVersion`."""

    name: str
    app_version: str | None = None
    prd_tag: str | None = None
    prd_version: str | None = None
    since: datetime | None = None
    at_least: bool = False
    problem: str | None = None

    @property
    def age_hours(self) -> float | None:
        if self.since is None:
            return None
        return (datetime.now(timezone.utc) - self.since).total_seconds() / 3600

    @property
    def age(self) -> str:
        hours = self.age_hours
        if hours is None:
            return "unknown"
        prefix = ">=" if self.at_least else ""
        if hours < 48:
            return f"{prefix}{hours:.0f}h"
        return f"{prefix}{hours / 24:.0f}d"


@dataclass
class Report:
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS
    level: list[Chart] = field(default_factory=list)
    settling: list[Chart] = field(default_factory=list)
    drifted: list[Chart] = field(default_factory=list)
    ahead: list[Chart] = field(default_factory=list)
    held: list[tuple[Chart, str]] = field(default_factory=list)
    unreadable: list[Chart] = field(default_factory=list)
    stale_holds: list[tuple[str, str]] = field(default_factory=list)

    @property
    def graded(self) -> int:
        return (
            len(self.level)
            + len(self.settling)
            + len(self.drifted)
            + len(self.ahead)
            + len(self.held)
            + len(self.unreadable)
        )

    @property
    def ok(self) -> bool:
        return not (self.drifted or self.ahead or self.unreadable or self.stale_holds)


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def discover_app_charts(charts_dir: Path) -> list[Path]:
    charts = []
    for chart_dir in sorted(p for p in charts_dir.iterdir() if p.is_dir()):
        if load_yaml(chart_dir / "Chart.yaml").get("type") == "library":
            continue
        charts.append(chart_dir)
    return charts


def strip_digest(tag: str) -> str:
    return DIGEST_SUFFIX_RE.sub("", tag)


def order_key(version: str) -> tuple[int, ...] | None:
    """Numeric release components, or None when the version cannot be ordered.
    Returning None rather than guessing keeps an unorderable pair out of the
    behind/ahead buckets, where the direction would be fabricated."""
    match = VERSION_RE.match(version)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def git_appversion_history(repo_root: Path, chart: str) -> list[tuple[datetime, str | None]]:
    """`appVersion` as it stood at each commit touching this chart's Chart.yaml,
    newest first, paired with that commit's committer date."""
    rel = f"charts/{chart}/Chart.yaml"
    log = subprocess.run(
        ["git", "-C", str(repo_root), "log", f"-{HISTORY_LIMIT}",
         "--format=%H%x09%cI", "--", rel],
        capture_output=True, text=True, check=True,
    ).stdout

    history = []
    for line in log.splitlines():
        if not line.strip():
            continue
        sha, _, stamp = line.partition("\t")
        blob = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{sha}:{rel}"],
            capture_output=True, text=True,
        )
        if blob.returncode != 0:
            continue
        parsed = yaml.safe_load(blob.stdout) or {}
        version = parsed.get("appVersion")
        history.append((
            datetime.fromisoformat(stamp).astimezone(timezone.utc),
            str(version) if version is not None else None,
        ))
    return history


def measure_drift(history: list[tuple[datetime, str | None]], prd_version: str) -> tuple[datetime | None, bool]:
    """When the current gap opened, and whether that is a lower bound.

    Walks newest to oldest for the most recent commit where `appVersion` still
    equalled what prd runs; the gap opened at the commit immediately after it.
    Reaching the end of the window without finding one means the gap predates
    the window, so the oldest inspected commit is reported as a lower bound
    rather than discarded."""
    for index, (_, version) in enumerate(history):
        if version == prd_version:
            return (history[index - 1][0], False) if index else (None, False)
    return (history[-1][0], True) if history else (None, False)


def load_holds(holds_file: Path) -> dict[tuple[str, str], str]:
    document = load_yaml(holds_file)
    entries = document.get("holds") or []
    if not isinstance(entries, list):
        raise SystemExit(f"{HOLDS_RELPATH}: 'holds' must be a list of entries.")

    holds = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SystemExit(f"{HOLDS_RELPATH}: every hold must be a mapping.")
        chart, tag, reason = entry.get("chart"), entry.get("prdTag"), entry.get("reason")
        if not chart or not tag or not reason:
            raise SystemExit(
                f"{HOLDS_RELPATH}: every hold needs 'chart', 'prdTag' and 'reason'; "
                f"got {entry!r}. A hold without a reason is an exemption nobody can review."
            )
        holds[(str(chart), str(tag))] = str(reason)
    return holds


def grade(chart_dir: Path) -> Chart:
    """Classify one chart from its two files alone, leaving age and holds to the
    caller. `problem` set means the pair could not be compared at all."""
    chart = Chart(name=chart_dir.name)

    chart_yaml = load_yaml(chart_dir / "Chart.yaml")
    app_version = chart_yaml.get("appVersion")
    chart.app_version = str(app_version) if app_version is not None else None

    prd_file = chart_dir / "values-prd.yaml"
    if not prd_file.exists():
        chart.problem = "no values-prd.yaml, so nothing states what prd runs"
        return chart

    image = load_yaml(prd_file).get("image") or {}
    raw_tag = image.get("tag") if isinstance(image, dict) else None
    chart.prd_tag = str(raw_tag) if raw_tag is not None else None

    if chart.app_version is None:
        chart.problem = "Chart.yaml declares no appVersion, so no release is recorded to compare against"
        return chart
    if not chart.prd_tag:
        chart.problem = (
            "values-prd.yaml sets no image.tag, so prd falls back to Chart.appVersion "
            "and follows every release without review"
        )
        return chart

    chart.prd_version = strip_digest(chart.prd_tag)
    if not chart.prd_version:
        chart.problem = f"image.tag {chart.prd_tag!r} carries a digest but no version in front of it"
    return chart


def check(repo_root: Path, history_reader=git_appversion_history) -> Report:
    holds = load_holds(repo_root / HOLDS_RELPATH)
    max_age_hours = float(os.environ.get("PRD_DRIFT_MAX_AGE_HOURS") or DEFAULT_MAX_AGE_HOURS)
    report = Report(max_age_hours=max_age_hours)
    consumed = set()

    for chart_dir in discover_app_charts(repo_root / "charts"):
        chart = grade(chart_dir)
        if chart.problem:
            report.unreadable.append(chart)
            continue

        if chart.prd_version == chart.app_version:
            report.level.append(chart)
            continue

        prd_key = order_key(chart.prd_version)
        app_key = order_key(chart.app_version)
        if prd_key is None or app_key is None:
            chart.problem = (
                f"cannot order appVersion {chart.app_version!r} against prd "
                f"{chart.prd_version!r}, so which one is behind is a guess"
            )
            report.unreadable.append(chart)
            continue

        chart.since, chart.at_least = measure_drift(
            history_reader(repo_root, chart.name), chart.prd_version
        )

        if prd_key > app_key:
            report.ahead.append(chart)
            continue

        reason = holds.get((chart.name, chart.prd_version))
        if reason is not None:
            consumed.add((chart.name, chart.prd_version))
            report.held.append((chart, reason))
        elif chart.age_hours is not None and chart.age_hours < max_age_hours:
            report.settling.append(chart)
        else:
            report.drifted.append(chart)

    report.stale_holds = sorted(set(holds) - consumed)
    return report


def table(charts: list[Chart]) -> list[str]:
    rows = [(c.name, c.app_version or "-", c.prd_version or "-", c.age) for c in charts]
    headers = ("chart", "appVersion", "prd", "behind for")
    widths = [max(len(row[i]) for row in (*rows, headers)) for i in range(4)]
    return [
        "  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in (headers, *rows)
    ]


def render(report: Report, full: bool = False) -> str:
    out = [
        f"prd drift: {report.graded} charts graded, {len(report.level)} level, "
        f"{len(report.drifted)} drifted, {len(report.settling)} settling, "
        f"{len(report.held)} held, {len(report.ahead)} ahead, "
        f"{len(report.unreadable)} unreadable "
        f"(threshold {report.max_age_hours:g}h)\n"
    ]

    if report.drifted:
        out.append("DRIFTED — released appVersion has not reached values-prd.yaml:")
        out.extend(table(report.drifted))
        out.append(
            "\nPromote with the Promote PRD workflow (workflow_dispatch, one app at a time — "
            "the promotion concurrency group holds only one pending run, so dispatching several "
            "at once cancels all but two). If a gap is deliberate, declare it with a reason in "
            f"{HOLDS_RELPATH}.\n"
        )

    if report.ahead:
        out.append("AHEAD — prd runs a version never released to non:")
        out.extend(table(report.ahead))
        out.append(
            "\nprd cannot have been promoted to this from here. Either appVersion was "
            "rolled back without rolling prd back, or values-prd.yaml was edited by hand.\n"
        )

    if report.unreadable:
        out.append("UNREADABLE — the two versions could not be compared:")
        for chart in report.unreadable:
            out.append(f"  charts/{chart.name}\n    {chart.problem}")
        out.append("")

    if report.stale_holds:
        out.append(f"STALE HOLDS — no longer match a drifted chart (remove them from {HOLDS_RELPATH}):")
        for name, tag in report.stale_holds:
            out.append(f"  {name} (prdTag={tag!r})")
        out.append("")

    if report.settling:
        out.append(f"Settling — behind, but for less than {report.max_age_hours:g}h:")
        out.extend(table(report.settling))
        out.append("")

    if report.held:
        out.append(f"Held (see {HOLDS_RELPATH}):")
        for chart, reason in report.held:
            out.append(
                f"  {chart.name}: appVersion {chart.app_version}, prd {chart.prd_version}, "
                f"behind for {chart.age}\n    reason: {reason}"
            )
        out.append("")

    if full and report.level:
        out.append("Level — prd runs the latest released version:")
        for chart in report.level:
            out.append(f"  {chart.name}: {chart.app_version}")
        out.append("")

    if report.ok:
        out.append(
            f"0 issues — no chart has been behind values-prd.yaml for more than "
            f"{report.max_age_hours:g}h."
        )

    return "\n".join(out)


def as_json(report: Report) -> str:
    def entry(chart: Chart, state: str, reason: str | None = None) -> dict:
        return {
            "chart": chart.name,
            "state": state,
            "appVersion": chart.app_version,
            "prdVersion": chart.prd_version,
            "prdTag": chart.prd_tag,
            "behindSince": chart.since.isoformat() if chart.since else None,
            "behindForHours": round(chart.age_hours, 2) if chart.age_hours is not None else None,
            "lowerBound": chart.at_least,
            "problem": chart.problem,
            **({"reason": reason} if reason is not None else {}),
        }

    charts = [
        *(entry(c, "level") for c in report.level),
        *(entry(c, "settling") for c in report.settling),
        *(entry(c, "drifted") for c in report.drifted),
        *(entry(c, "ahead") for c in report.ahead),
        *(entry(c, "held", reason) for c, reason in report.held),
        *(entry(c, "unreadable") for c in report.unreadable),
    ]
    return json.dumps(
        {
            "ok": report.ok,
            "maxAgeHours": report.max_age_hours,
            "counts": {
                "graded": report.graded,
                "level": len(report.level),
                "settling": len(report.settling),
                "drifted": len(report.drifted),
                "ahead": len(report.ahead),
                "held": len(report.held),
                "unreadable": len(report.unreadable),
                "staleHolds": len(report.stale_holds),
            },
            "charts": sorted(charts, key=lambda c: c["chart"]),
            "staleHolds": [{"chart": n, "prdTag": t} for n, t in report.stale_holds],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report charts whose released appVersion never reached values-prd.yaml."
    )
    parser.add_argument(
        "--max-age-hours", type=float, default=None,
        help="hours a chart may sit behind before it counts as a finding "
             f"(default {DEFAULT_MAX_AGE_HOURS}, or PRD_DRIFT_MAX_AGE_HOURS)",
    )
    parser.add_argument("--full", action="store_true", help="also list charts that are level")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = parser.parse_args(argv)

    if args.max_age_hours is not None:
        os.environ["PRD_DRIFT_MAX_AGE_HOURS"] = str(args.max_age_hours)

    repo_root = Path(__file__).resolve().parent.parent
    report = check(repo_root)
    print(as_json(report) if args.json else render(report, full=args.full))
    return EXIT_LEVEL if report.ok else EXIT_DRIFTED


if __name__ == "__main__":
    # Three exit codes, not two. A caller has to be able to tell "prd is behind"
    # from "this check could not say", because they call for opposite responses:
    # the first is a promotion someone has to dispatch, the second means drift
    # detection is blind and nobody would otherwise learn that. Collapsing an
    # unhandled exception into the same 1 that a finding uses is what makes a
    # broken checker look exactly like a working one with something to report.
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(EXIT_NO_VERDICT)
