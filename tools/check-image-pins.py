#!/usr/bin/env python3
"""Reject mutable (tag-only) container image references in charts/*/values-*.yaml.

Every app chart under charts/ (excluding the charts/common library chart) is
deployed with a values.yaml base plus a values-<env>.yaml overlay per
environment. The overlay decides the image actually pulled at runtime: a bare
tag (or no tag at all, falling back to Chart.appVersion) is mutable — a
republished tag or a chart bump silently changes what a node pulls next,
independent of anything committed to this repo. That already caused an
outage: a reused `1.0.0` servicediscovery tag was republished, and
`imagePullPolicy: IfNotPresent` meant nodes kept serving a build predating the
`/api/remotes` handler while every probe stayed green.

This check requires every values-<env>.yaml image.tag to be digest-pinned
(`<tag>@sha256:<64 hex>`) or listed in tools/image-pin-allowlist.yaml with an
inline reason. Nothing is exempted silently.

IMPORTANT — this check validates *format* only (a digest anchor is present),
never that the digest matches what a registry currently serves. It never
fetches a registry. If something else in this pipeline ever adds live digest
verification (e.g. re-resolving a tag to confirm its pin), it MUST compare
against the registry's manifest-list / OCI index digest, not a per-arch
child manifest digest. A `docker-content-digest` response header for a
multi-arch tag is the index digest; a running pod's `.status...imageID`
reports the digest of the single-arch child manifest it pulled. Comparing
those two digests manufactures drift that does not exist (see the
prd-digest-pinning epic postmortem) — this script deliberately never makes
that comparison because it never resolves anything over the network.

Usage:
    python3 tools/check-image-pins.py

Exit codes: 0 = no unexplained violations; 1 = one or more tag-only image
references outside the allowlist, or a malformed allowlist entry.
"""
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CHARTS_DIR = REPO_ROOT / "charts"
ALLOWLIST_FILE = REPO_ROOT / "tools/image-pin-allowlist.yaml"
ENVS = ("non", "prd")

# Matches a digest-pinned tag: "<anything>@sha256:<64 lowercase hex>" at the
# end of the string. Presence-only check — see module docstring for why this
# script never resolves or compares the digest itself.
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def discover_charts() -> list[str]:
    charts = []
    for chart_dir in sorted(CHARTS_DIR.iterdir()):
        if not chart_dir.is_dir():
            continue
        chart_yaml = load_yaml(chart_dir / "Chart.yaml")
        if chart_yaml.get("type") == "library":
            continue
        charts.append(chart_dir.name)
    return charts


def effective_image(chart: str) -> dict[str, tuple[Path, str, str]]:
    """Return {env: (values_file, repository, tag)} using Helm's own override
    rule for the image map: keys set in values-<env>.yaml win, otherwise fall
    back to values.yaml. An overlay that never mentions `image` at all still
    inherits the base file's (usually unpinned placeholder) tag — that is a
    real, effective image reference, not something to skip.
    """
    base_image = load_yaml(CHARTS_DIR / chart / "values.yaml").get("image", {}) or {}
    results = {}
    for env in ENVS:
        values_file = CHARTS_DIR / chart / f"values-{env}.yaml"
        if not values_file.exists():
            continue
        overlay_image = load_yaml(values_file).get("image", {}) or {}
        tag = overlay_image.get("tag", base_image.get("tag", ""))
        repository = overlay_image.get("repository", base_image.get("repository", ""))
        results[env] = (values_file, repository, tag)
    return results


def load_allowlist() -> dict[str, dict]:
    if not ALLOWLIST_FILE.exists():
        return {}
    data = yaml.safe_load(ALLOWLIST_FILE.read_text(encoding="utf-8")) or {}
    entries = data.get("exceptions", [])
    by_path = {}
    for i, entry in enumerate(entries):
        path = entry.get("path")
        reason = entry.get("reason")
        if not path:
            raise SystemExit(f"{ALLOWLIST_FILE}: exceptions[{i}] missing required 'path'")
        if not reason or not reason.strip():
            raise SystemExit(
                f"{ALLOWLIST_FILE}: exceptions[{i}] (path={path}) missing a non-empty 'reason' — "
                "every exception must document inline why it is deliberate"
            )
        if path in by_path:
            raise SystemExit(f"{ALLOWLIST_FILE}: duplicate exception entry for path={path}")
        by_path[path] = entry
    return by_path


def main() -> int:
    allowlist = load_allowlist()
    consumed_allowlist_paths = set()

    pinned, allowed, violations = [], [], []

    for chart in discover_charts():
        for env, (values_file, repository, tag) in effective_image(chart).items():
            relpath = values_file.relative_to(REPO_ROOT).as_posix()
            is_pinned = bool(tag) and DIGEST_RE.search(tag) is not None
            ref_desc = f"{repository}:{tag}" if tag else f"{repository} (no tag override — falls back to Chart.appVersion)"

            if is_pinned:
                pinned.append((chart, env, ref_desc))
                continue

            entry = allowlist.get(relpath)
            if entry is not None:
                consumed_allowlist_paths.add(relpath)
                allowed.append((chart, env, ref_desc, entry["reason"]))
            else:
                violations.append((chart, env, relpath, ref_desc))

    stale = sorted(set(allowlist) - consumed_allowlist_paths)

    print(f"image-pin inventory: {len(pinned)} digest-pinned, {len(allowed)} allowlisted, {len(violations)} unexplained\n")

    if pinned:
        print("Digest-pinned:")
        for chart, env, ref_desc in pinned:
            print(f"  {chart} ({env}): {ref_desc}")
        print()

    if allowed:
        print("Allowlisted (see tools/image-pin-allowlist.yaml):")
        for chart, env, ref_desc, reason in allowed:
            print(f"  {chart} ({env}): {ref_desc}\n    reason: {reason}")
        print()

    exit_code = 0

    if violations:
        exit_code = 1
        print("VIOLATIONS — mutable image reference with no digest pin and no allowlist entry:")
        for chart, env, relpath, ref_desc in violations:
            print(f"  {chart} ({env}) [{relpath}]: {ref_desc}")
        print(
            "\nFix by pinning image.tag to '<tag>@sha256:<index digest>' in the file above, "
            f"or add a documented exception to {ALLOWLIST_FILE.relative_to(REPO_ROOT).as_posix()}."
        )

    if stale:
        exit_code = 1
        print("\nSTALE ALLOWLIST ENTRIES — no longer match an unpinned reference (remove them):")
        for relpath in stale:
            print(f"  {relpath}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
