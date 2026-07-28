# prd Promotion

prd image tags change **only** through pull requests opened by the
[`Promote PRD` workflow](../.github/workflows/promote-prd.yml). Hand-editing
`charts/*/values-prd.yaml` is forbidden — the file is code-owned and a
prd-touching PR cannot merge without an explicit code-owner review.

## Versioning contract

Three version fields exist per chart and they mean different things:

| Field | Meaning | Who changes it |
|---|---|---|
| `Chart.yaml` `version` | Chart **packaging** version. Bumped only when templates, values structure, or chart dependencies change. Drives the Helm repo release (`Release Chart` workflow on `*-v[0-9]*` tags). | Humans, in the PR that changes the chart itself |
| `Chart.yaml` `appVersion` | Latest app release delivered to **non**. `image.tag` is empty in `values.yaml`/`values-non.yaml`, so non runs `appVersion`. | The apps-repo release pipeline (direct bot commit on merge to apps `main`) |
| `values-prd.yaml` `image.tag` | The image **prd** actually runs. | The `Promote PRD` workflow, via reviewed PR only |

Historical note: chart `version` used to be hand-bumped to mirror the prd
image tag (that is why, before this convention landed, every chart's
`version` equals its prd pin). That mirroring is **stopped**: promotion PRs
never touch `Chart.yaml`, and `version` must only move for packaging changes.
Mirroring an app tag into `version` pollutes the Helm repo release stream
with releases whose chart content did not change.

## How promotion works

1. The apps-repo pipeline releases a project, pushes its image, bumps the
   chart `appVersion` here (direct bot commit — unchanged, non-only), and
   dispatches the `apps-deployed` event. (The dispatch is currently a no-op:
   the `E2E` workflow is manual-only because it needs the dormant self-hosted
   ARC runner — see the note in `e2e.yml`.)
2. The `E2E` workflow runs the platform E2E suite against non — today via
   `workflow_dispatch` with the published apps SHA, once ARC is re-enabled.
3. On E2E **success**, `Promote PRD` runs (it also supports direct
   `workflow_dispatch` — the current day-to-day path):
   - For every chart listed in [`.github/prd-auto-promote`](../.github/prd-auto-promote)
     whose `appVersion` differs from the pinned prd tag, it opens (or
     force-refreshes) a single-commit PR on branch `chore/promote-<app>-prd`
     changing only `image.tag` in that chart's `values-prd.yaml`.
   - Charts not in the allowlist are never auto-proposed.
4. Normal CI (lint, template with the prd values stack, validate-config) runs
   on the PR. The code owner reviews and merges; ArgoCD syncs prd.

Manual promotion (including to a tag other than `appVersion`):

```bash
gh workflow run promote-prd.yml -R jdwlabs/deployments \
  -f app=<chart> [-f tag=<image-tag>]
```

## Digest pinning

`values-prd.yaml` may only ever hold `<tag>@sha256:<digest>`, never a bare
tag. A bare tag is a mutable reference: republishing it changes what a node
pulls next, and under `imagePullPolicy: IfNotPresent` already-warm nodes go on
serving the old build while every probe stays green. That is not theoretical —
it is how a republished `servicediscovery:1.0.0` kept prd serving a binary
that predated the `/api/remotes` handler. `tools/check-image-pins.py` fails CI
on any unpinned prd reference, so a promotion PR carrying a bare tag cannot
merge in the first place.

The promotion workflow therefore resolves the tag itself, whichever path
supplied it:

| Tag source | What gets written to `values-prd.yaml` |
|---|---|
| Chart `appVersion` (auto-promotion, or dispatch with no `-f tag=`) | `appVersion@sha256:<index digest>`, resolved against the registry |
| `-f tag=<bare tag>` | `<bare tag>@sha256:<index digest>`, resolved against the registry |
| `-f tag=<tag>@sha256:<digest>` | used verbatim — the digest was chosen deliberately (a rollback pins a digest the tag no longer points at), so it is never re-resolved |

Resolution is `tools/resolve-image-digest.sh <repository> <tag>`, which reads
the registry manifest API and prints the digest of the manifest the tag
addresses. If it cannot resolve, the promotion **fails** — it never falls back
to the bare tag, because a fallback is exactly the mutable reference the pin
exists to prevent. Common causes and fixes:

- *tag does not exist* — the release pipeline has not pushed it yet; re-run
  the promotion once the image is published.
- *registry unreachable / rate-limited* — the resolver already retries; re-run,
  or dispatch with an explicit `-f tag='<tag>@sha256:<index digest>'`.
- *private repository* — the resolver only does anonymous pulls; registry
  credentials would have to be wired into the workflow.

### Index digest, never a child digest

For a multi-arch tag the pinned digest is the **manifest index** (manifest
list) digest, not a per-architecture child manifest digest. Both are valid
pull references, so a child digest deploys fine and then quietly poisons every
later comparison: a pod reports the child manifest its node pulled in
`.status.containerStatuses[].imageID`, `docker buildx imagetools inspect`
reports the index, and containerd deduplicates by config digest — so a
child-pinned chart reads as drift against tooling that is in fact in sync. The
resolver offers index media types first and never sends a platform hint, and
it refuses any response it cannot classify. A tag that genuinely has no index
(single-platform image) resolves to its only digest, and the run log says so
explicitly.

### Guardrails

- One chart per PR; the workflow verifies the diff is exactly one line — and
  that the line is the image tag — and aborts that chart otherwise.
- `app` and `tag` values are validated up front (chart-name and Docker image
  tag grammar) before they reach any file path, git ref, or edit command.
- Every candidate is re-checked for a digest before any PR is opened, so a
  bare tag cannot reach `values-prd.yaml` even if the resolution step is later
  changed to permit one.
- The promotion branch is rebuilt from `main` on every run — reruns are
  idempotent and PRs never accumulate stale commits.
- The commit is created through the GitHub contents API, so it is signed and
  attributable to the bot App.
- The `PRD Promotion Review Gate` ruleset plus `.github/CODEOWNERS` block
  merging any prd-path change without a code-owner review.

### Requirements

- Repository secrets `RELEASE_APP_ID` and `RELEASE_APP_PRIVATE_KEY` (same
  GitHub App the apps repo uses for delivery). Without them the workflow
  falls back to `GITHUB_TOKEN`: the PR still opens, but CI must be triggered
  manually (`gh workflow run ci.yaml --ref chore/promote-<app>-prd`) because
  GitHub suppresses workflow runs for events created by `GITHUB_TOKEN`.
- The App needs **Contents: Read & write** and **Pull requests: Read &
  write** on this repository.

## Rollback

Revert the promotion PR. The revert re-pins the previous tag and ArgoCD syncs
it like any other merge. Image tags are never deleted from the registry, so a
revert is always executable.

That invariant covers every tag, including ones no environment points at any
more. `jdwlabs/rolesui:0.5.4-promotion-demo` — an artifact of a promotion
workflow demonstration that briefly served prd — is retained for this reason
rather than by oversight. Deleting it would buy tidiness at the cost of the
guarantee above, and the guarantee is what makes a rollback dependable at the
moment it is actually needed. Provenance, not deletion, is the control: prd
pins digests, so what a tag name suggests never decides what runs.

## Backlog burn-down plan (current prd gap)

**Status: plan only — do not execute until the two-generation divergence is
resolved.**

prd pins images from the previous release line on all six apps, while
`appVersion` tracks the re-baselined main line:

| Chart | prd pin (legacy line) | appVersion (main line) |
|---|---|---|
| authui | 2.0.1 | 2.0.0 |
| container | 1.4.1 | 2.0.0 |
| usersui | 1.3.5 | 2.0.0 |
| rolesui | 0.5.4 | 1.0.0 |
| usersrole | 0.10.14 | 1.0.0 |
| servicediscovery | 1.0.0 | 1.0.0 |

The pinned legacy images are **functionally ahead** of the main line for some
apps: the legacy frontend generation consumes `/api/route-remotes` with the
newer module-federation runtime, while the current main line consumes
`/api/remotes`. No app is verified converged. A numerically newer tag is
therefore **not** evidence that promotion is safe — promoting `appVersion`
today could be a functional downgrade.

### Phase 0 — convergence verification (blocking prerequisite)

For each app, before it may be promoted or allowlisted:

1. Diff the pinned legacy image's behavior against the main-line image in
   non: remotes contract (`/api/route-remotes` vs `/api/remotes`), module
   federation runtime, and user-visible feature set.
2. Land any missing legacy-line functionality on apps `main` (source
   convergence), release it, and let the bot bump `appVersion` here.
3. Record the verification outcome per app before enabling it.

### Phase 1 — decoupled services first

`servicediscovery` and `usersrole` have no module-federation coupling.
Once verified converged: promote one at a time via a bot PR, merge, and soak
in prd (suggested: 24h, watch error rates and probes) before the next app.
Note prd resource overrides in `values-prd.yaml` stay as-is — promotion PRs
only move `image.tag`.

### Phase 2 — the micro-frontend set

`container` (shell) and `authui`/`rolesui`/`usersui` (remotes) share the
remotes-API contract, and the generation switch changes that contract. They
cannot be promoted independently across the generation boundary:

1. Verify in non that the main-line shell + all main-line remotes work as a
   set (this is what the E2E suite exercises).
2. Promote the set in one coordinated window: one bot PR per app (reviewable
   independently), merged consecutively, with ArgoCD expected to converge
   within the window. Schedule in a low-traffic period.
3. Keep the previous pins recorded in each PR body; rollback is reverting the
   set in reverse order.

### Phase 3 — enable steady-state auto-promotion

After the gap is closed, add apps to `.github/prd-auto-promote` one at a
time. From then on every passing non E2E run proposes at most a one-version
step per app, and the review burden per PR is small.
