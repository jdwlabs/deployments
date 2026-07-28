#!/usr/bin/env python3
"""Reject mutable (tag-only) container image references in this repo's charts.

Every app chart under charts/ (excluding the charts/common library chart) is
deployed with a values.yaml base plus a values-<env>.yaml overlay per
environment. The overlay decides the image actually pulled at runtime: a bare
tag (or no tag at all, falling back to Chart.appVersion) is mutable — a
republished tag or a chart bump silently changes what a node pulls next,
independent of anything committed to this repo. That already caused an
outage: a reused `1.0.0` servicediscovery tag was republished, and
`imagePullPolicy: IfNotPresent` meant nodes kept serving a build predating the
`/api/remotes` handler while every probe stayed green.

Scope — everything below is checked, and a surface that yields nothing is a
failure rather than a silent pass:

  * every charts/<chart>/values-<env>.yaml, discovered by glob. No environment
    name is hard-coded, so adding charts/<chart>/values-dev.yaml puts it under
    the check immediately instead of leaving it unexamined.
  * a non-library chart with no values-<env>.yaml overlay at all, which would
    otherwise contribute zero references and read as "clean".
  * every image reference reachable in the Helm-merged (values.yaml deep-merged
    with the overlay) result, not only the top-level `image` map: a
    `sidecar.image.{repository,tag}` map or an `initContainer.image: repo:tag`
    string is the same mutable reference with the same blast radius.
  * literal `image:` lines in chart templates under charts/*/templates/. Lines
    whose value is a `{{ ... }}` expression are skipped — those resolve from
    values, which the values scan already covers.

Every discovered reference must be digest-pinned (`<tag>@sha256:<64 hex>`) or
listed in tools/image-pin-allowlist.yaml with an inline reason. Nothing is
exempted silently. An allowlist entry is keyed on (path, repository, tag), so
it stops applying the moment the reference it excused changes into a different
one — an exception cannot outlive the thing it was written for.

Malformed references are reported separately and are NOT allowlistable, because
no reason makes an unresolvable reference correct:

  * a digest-only tag (`tag: "@sha256:<64 hex>"`), which passes a naive
    "contains a digest" test but is unusable: the common chart renders
    `"{{ .repository }}:{{ .tag | default .Chart.AppVersion }}"`
    unconditionally, producing `repo:@sha256:...` — an empty tag followed by
    junk. `helm template` and `helm lint` both accept it; only the kubelet
    rejects it, at pull time, in the cluster.
  * an unquoted numeric tag (`tag: 1.0`), which YAML parses as a float before
    anything sees it as a version. `tag: 1.10` becomes 1.1 and silently points
    at a different image than the author typed.

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
references outside the allowlist, a malformed reference, an unchecked chart,
a stale allowlist entry, or a malformed allowlist entry.
"""
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ALLOWLIST_RELPATH = "tools/image-pin-allowlist.yaml"

# Matches a digest-pinned reference: "<anything>@sha256:<64 lowercase hex>" at
# the end of the string. Presence-only check — see module docstring for why
# this script never resolves or compares the digest itself.
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")

# The digest component on its own, for validating what follows an '@'.
DIGEST_COMPONENT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# OCI/Docker reference grammar for the tag component. A tag may not start with
# '.' or '-', and may not contain '@' or ':' — which is exactly why a
# digest-only tag cannot be concatenated into a valid reference.
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")

# A literal `image:` line in a chart template. `[^#]+?` stops before a trailing
# comment; `imagePullPolicy`/`imagePullSecrets` do not match because the colon
# must follow `image` directly.
TEMPLATE_IMAGE_RE = re.compile(r"^\s*-?\s*image:\s*(?P<value>[^#]+?)\s*(?:#.*)?$")

TEMPLATE_SUFFIXES = (".yaml", ".yml", ".tpl")


@dataclass
class Ref:
    """One image reference, normalised so values maps and raw strings compare
    the same way. `path` is the file an author would edit to fix it, which for
    a values overlay is the overlay even when the value was inherited from
    values.yaml."""

    path: str
    line: int | None
    where: str
    repository: str
    tag: str
    rendered: str
    problem: str | None = None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.repository, self.tag)


@dataclass
class Report:
    pinned: list[Ref] = field(default_factory=list)
    allowed: list[tuple[Ref, str]] = field(default_factory=list)
    violations: list[Ref] = field(default_factory=list)
    malformed: list[Ref] = field(default_factory=list)
    unchecked_charts: list[str] = field(default_factory=list)
    stale: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.violations or self.malformed or self.unchecked_charts or self.stale)


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def deep_merge(base, overlay):
    """Helm's coalesce rule for values: maps merge key-by-key, everything else
    (scalars, lists) is replaced wholesale by the overlay. An explicit null in
    the overlay clears the base value, which normalise_tag then treats as an
    absent tag — the same fallback Helm applies."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = deep_merge(base.get(key), value) if key in base else value
        return merged
    return overlay


def bare_repository(image_ref: str) -> str:
    """Strip tag/digest from an image reference, e.g.
    'ghcr.io/foo/bar:v1@sha256:abc' -> 'ghcr.io/foo/bar', without mistaking a
    registry host's port (before the first '/') for a tag separator."""
    ref = image_ref.split("@", 1)[0]
    tail = ref[ref.rfind("/") + 1 :]
    if ":" in tail:
        return ref[: ref.rfind(":")]
    return ref


def split_raw_reference(image_ref: str) -> tuple[str, str]:
    """Split a raw 'repo[:tag][@digest]' string into (repository, tag) where
    the tag carries any digest suffix, matching how a values `tag:` field
    carries one."""
    repository = bare_repository(image_ref)
    remainder = image_ref[len(repository) :]
    return repository, remainder.lstrip(":")


def normalise_tag(raw) -> tuple[str, str | None]:
    """Return (tag, problem). YAML turns unquoted scalars into Python types
    before any of this code sees them, so a numeric-looking tag arrives as an
    int or float. Coercing to str keeps the check from crashing, but the
    coercion itself is lossy (1.10 -> 1.1), so it is also reported."""
    if raw is None:
        return "", None
    if isinstance(raw, str):
        return raw, None
    if isinstance(raw, bool):
        return str(raw).lower(), f"tag is an unquoted YAML boolean ({raw!r}) — quote it"
    if isinstance(raw, (int, float)):
        return (
            str(raw),
            f"tag is an unquoted YAML number, parsed as {raw!r} before this check or Helm "
            "ever saw it as a version string (1.10 becomes 1.1) — quote it",
        )
    return str(raw), f"tag is a {type(raw).__name__}, not a string — quote it"


def reference_problem(repository: str, tag: str) -> str | None:
    """Reject references the common chart would render into something no
    registry can resolve. Checked before the digest test, because a digest-only
    tag contains a well-formed digest and would otherwise read as pinned."""
    if tag.startswith("@"):
        return (
            "digest-only tag. charts/common/templates/_deployment.yaml renders "
            '"{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}" '
            f"unconditionally, so this becomes '{repository}:{tag}' — an empty tag followed by a "
            "digest, which no registry can resolve. helm template and helm lint both accept it. "
            "Use '<tag>@sha256:<index digest>'"
        )
    if not tag:
        return None
    name, sep, digest = tag.partition("@")
    if sep and not DIGEST_COMPONENT_RE.match(digest):
        return f"digest component '@{digest}' is not a well-formed '@sha256:<64 lowercase hex>'"
    if not TAG_RE.match(name):
        return f"tag '{name}' is not a valid OCI tag ([A-Za-z0-9_][A-Za-z0-9._-]{{0,127}})"
    return None


def find_line(text: str, needle: str) -> int | None:
    """First 1-based line number containing needle, or None. Best-effort: a
    reference inherited from values.yaml has no line in the overlay."""
    for i, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return i
    return None


def walk_values(node, found: list[tuple[str, str, object, str]], trail: str = "", key=None) -> None:
    """Collect (where, repository, raw_tag, rendered_hint) for every image
    reference in a merged values tree — Helm-style {repository, tag} maps and
    raw `image: repo:tag` strings alike, at any depth."""
    if isinstance(node, dict):
        repository = node.get("repository")
        if isinstance(repository, str) and repository and ("tag" in node or key == "image"):
            found.append((trail or "image", repository, node.get("tag"), "map"))
        image = node.get("image")
        if isinstance(image, str) and image.strip():
            found.append((f"{trail}.image" if trail else "image", image.strip(), None, "raw"))
        for child_key, value in node.items():
            walk_values(value, found, f"{trail}.{child_key}" if trail else str(child_key), child_key)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            walk_values(item, found, f"{trail}[{i}]", None)


def refs_from_values(chart_dir: Path, overlay: Path, repo_root: Path) -> list[Ref]:
    merged = deep_merge(load_yaml(chart_dir / "values.yaml"), load_yaml(overlay))
    relpath = overlay.relative_to(repo_root).as_posix()
    text = overlay.read_text(encoding="utf-8")

    refs = []
    found: list[tuple[str, str, object, str]] = []
    walk_values(merged, found)
    for where, repository, raw_tag, shape in found:
        if shape == "raw":
            repository, tag = split_raw_reference(repository)
            problem = None
        else:
            tag, problem = normalise_tag(raw_tag)
        problem = problem or reference_problem(repository, tag)
        rendered = f"{repository}:{tag}" if tag else f"{repository} (no tag — falls back to Chart.appVersion)"
        refs.append(
            Ref(
                path=relpath,
                line=find_line(text, tag) if tag else find_line(text, repository),
                where=where,
                repository=repository,
                tag=tag,
                rendered=rendered,
                problem=problem,
            )
        )
    return refs


def refs_from_templates(charts_dir: Path, repo_root: Path) -> list[Ref]:
    refs = []
    for template in sorted(charts_dir.glob("*/templates/**/*")):
        if not template.is_file() or template.suffix not in TEMPLATE_SUFFIXES:
            continue
        relpath = template.relative_to(repo_root).as_posix()
        for i, line in enumerate(template.read_text(encoding="utf-8").splitlines(), start=1):
            match = TEMPLATE_IMAGE_RE.match(line)
            if not match:
                continue
            value = match.group("value").strip().strip("\"'").strip()
            # A templated value resolves from values.yaml, which the values
            # scan already covers with the real per-environment overrides.
            if not value or "{{" in value:
                continue
            repository, tag = split_raw_reference(value)
            refs.append(
                Ref(
                    path=relpath,
                    line=i,
                    where="image",
                    repository=repository,
                    tag=tag,
                    rendered=value if tag else f"{value} (no tag — implicit :latest)",
                    problem=reference_problem(repository, tag),
                )
            )
    return refs


def discover_app_charts(charts_dir: Path) -> list[Path]:
    charts = []
    for chart_dir in sorted(p for p in charts_dir.iterdir() if p.is_dir()):
        if load_yaml(chart_dir / "Chart.yaml").get("type") == "library":
            continue
        charts.append(chart_dir)
    return charts


def load_allowlist(allowlist_file: Path) -> dict[tuple[str, str, str], dict]:
    if not allowlist_file.exists():
        return {}
    data = yaml.safe_load(allowlist_file.read_text(encoding="utf-8")) or {}
    by_key = {}
    for i, entry in enumerate(data.get("exceptions", []) or []):
        path = entry.get("path")
        repository = entry.get("repository")
        reason = entry.get("reason")
        if not path or not repository:
            raise SystemExit(
                f"{allowlist_file}: exceptions[{i}] missing required 'path' and/or 'repository'"
            )
        raw_tag = entry.get("tag", "")
        if raw_tag is not None and not isinstance(raw_tag, str):
            raise SystemExit(
                f"{allowlist_file}: exceptions[{i}] (path={path}) has a non-string 'tag' "
                f"({raw_tag!r}) — quote it so it keys against the reference verbatim"
            )
        tag = raw_tag or ""
        if not reason or not reason.strip():
            raise SystemExit(
                f"{allowlist_file}: exceptions[{i}] (path={path}, repository={repository}) "
                "missing a non-empty 'reason' — every exception must document inline why it "
                "is deliberate"
            )
        key = (path, repository, tag)
        if key in by_key:
            raise SystemExit(f"{allowlist_file}: duplicate exception entry for {key}")
        by_key[key] = entry
    return by_key


def check(repo_root: Path) -> Report:
    charts_dir = repo_root / "charts"
    allowlist = load_allowlist(repo_root / ALLOWLIST_RELPATH)
    consumed = set()
    report = Report()

    refs: list[Ref] = []
    for chart_dir in discover_app_charts(charts_dir):
        overlays = sorted(chart_dir.glob("values-*.yaml"))
        if not overlays:
            report.unchecked_charts.append(chart_dir.relative_to(repo_root).as_posix())
            continue
        for overlay in overlays:
            refs.extend(refs_from_values(chart_dir, overlay, repo_root))
    refs.extend(refs_from_templates(charts_dir, repo_root))

    for ref in refs:
        if ref.problem:
            report.malformed.append(ref)
            continue
        if ref.tag and DIGEST_RE.search(ref.tag):
            report.pinned.append(ref)
            continue
        entry = allowlist.get(ref.key)
        if entry is not None:
            consumed.add(ref.key)
            report.allowed.append((ref, entry["reason"]))
        else:
            report.violations.append(ref)

    report.stale = sorted(set(allowlist) - consumed)
    return report


def render(report: Report) -> str:
    out = [
        f"image-pin inventory: {len(report.pinned)} digest-pinned, {len(report.allowed)} allowlisted, "
        f"{len(report.violations)} unexplained, {len(report.malformed)} malformed\n"
    ]

    if report.pinned:
        out.append("Digest-pinned:")
        for ref in report.pinned:
            out.append(f"  [{ref.location}] {ref.where}: {ref.rendered}")
        out.append("")

    if report.allowed:
        out.append(f"Allowlisted (see {ALLOWLIST_RELPATH}):")
        for ref, reason in report.allowed:
            out.append(f"  [{ref.location}] {ref.where}: {ref.rendered}\n    reason: {reason}")
        out.append("")

    if report.malformed:
        out.append("MALFORMED — reference cannot resolve; not allowlistable:")
        for ref in report.malformed:
            out.append(f"  [{ref.location}] {ref.where}: {ref.rendered}\n    {ref.problem}")
        out.append("")

    if report.violations:
        out.append("VIOLATIONS — mutable image reference with no digest pin and no allowlist entry:")
        for ref in report.violations:
            out.append(f"  [{ref.location}] {ref.where}: {ref.rendered}")
        out.append(
            "\nFix by pinning to '<tag>@sha256:<index digest>' at the location above, or add a "
            f"documented exception (path + repository + tag + reason) to {ALLOWLIST_RELPATH}.\n"
        )

    if report.unchecked_charts:
        out.append("UNCHECKED CHARTS — no values-<env>.yaml overlay, so nothing was verified:")
        for chart in report.unchecked_charts:
            out.append(f"  {chart}")
        out.append("")

    if report.stale:
        out.append("STALE ALLOWLIST ENTRIES — no longer match an unpinned reference (remove them):")
        for path, repository, tag in report.stale:
            out.append(f"  {path} ({repository}, tag={tag!r})")
        out.append("")

    if report.ok:
        out.append("0 issues — every image reference is digest-pinned or documented.")

    return "\n".join(out)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    report = check(repo_root)
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
