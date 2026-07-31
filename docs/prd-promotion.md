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

## Current prd position

The authoritative pins are the chart files themselves, never a table in this
document — a duplicated table goes stale every release and then reads as a
hard stop that isn't one. Two commands answer "what is where":

```bash
grep -H 'tag:' charts/*/values-prd.yaml     # what prd runs (digest-pinned)
grep -H appVersion charts/*/Chart.yaml      # what non runs
```

Five charts — `authui`, `container`, `rolesui`, `servicediscovery`, and
`usersui` — track their current major line. They trail `appVersion` by at most
a patch or two, which is ordinary promotion lag rather than a version gap, and
`servicediscovery` is exactly level with it. For these, a numerically newer
`appVersion` is a normal promotion candidate: the frontend generation split
that once made the pinned image functionally ahead of the main line — the
`/api/route-remotes` versus `/api/remotes` remotes contract — is closed.

`usersrole` is the exception and the one real gap. Its prd pin is still on the
`0.x` line while `appVersion` has moved to `1.x`, so it remains a generation
behind and needs a promotion of its own. It is also the only chart to which
the cross-generation verification below still applies; for the other five that
work is finished, not pending.

`.github/prd-auto-promote` is empty, but that emptiness is not what holds
auto-promotion back today. The `apps-deployed` → E2E → `Promote PRD` chain
does not fire at all while the E2E workflow is manual-only (see [How promotion
works](#how-promotion-works)), so populating the allowlist would change
nothing until that chain runs. Promotion is a `workflow_dispatch` in the
meantime.

## Promotion sequencing

### Verification before a cross-generation promotion

Applies to `usersrole`, and to any future chart whose prd pin and `appVersion`
diverge by a major version:

1. Diff the pinned image's behavior against the main-line image in non: API
   contract, module federation runtime, and user-visible feature set.
2. Land any functionality that exists only in the pinned line on apps `main`
   (source convergence), release it, and let the bot bump `appVersion` here.
3. Record the verification outcome before promoting.

A promotion within the same major line does not need this. It is a patch step,
and CI plus the code-owner review are the controls.

### Phase 1 — decoupled services first

`servicediscovery` and `usersrole` have no module-federation coupling, so they
promote independently of the rest. `servicediscovery` is already level;
`usersrole` is the outstanding one. Promote one at a time via a bot PR, merge,
and soak in prd (suggested: 24h, watch error rates and probes) before the next
app. Note prd resource overrides in `values-prd.yaml` stay as-is — promotion
PRs only move `image.tag`.

### Phase 2 — the micro-frontend set

`container` (shell) and `authui`/`rolesui`/`usersui` (remotes) share the
remotes-API contract. They crossed onto the current line together, and they
move as a set whenever that contract changes:

1. Verify in non that the shell and all remotes work as a set (this is what
   the E2E suite exercises).
2. Promote the set in one coordinated window: one bot PR per app (reviewable
   independently), merged consecutively, with ArgoCD expected to converge
   within the window. Schedule in a low-traffic period.
3. Keep the previous pins recorded in each PR body; rollback is reverting the
   set in reverse order.

A patch step that leaves the remotes contract untouched does not need the full
coordinated window, but keeping the four aligned is cheaper than reasoning
each time about which pairings are safe to split.

### Phase 3 — enable steady-state auto-promotion

Once the E2E trigger chain is live again, add apps to
`.github/prd-auto-promote` one at a time — `servicediscovery` first, being
decoupled and already level. From then on every passing non E2E run proposes
at most a one-version step per app, and the review burden per PR is small.

## Why the release App keeps a pull-request-scoped bypass

The release App reaches `main` only through a pull request it opens and
auto-merges. Its ruleset bypass is `pull_request`, not `always`: it may merge a
pull request that has no human approval, but it cannot push to `main` directly.

The bypass cannot be removed outright. All three rulesets protecting `main`
require one approving review, and a GitHub App cannot approve its own pull
request — so removing it would need a second approving identity, which is the
same standing exception one layer up. A path-scoped bypass is not available
either: bypass actors are scoped to the ruleset, and a bypass actor skips rules,
so a file-path rule could never constrain the App.

Merged commits on `main` are unsigned despite `required_signatures`, because
rebase-merge re-creates commits and discards the signature GitHub applies to the
API-minted commit on the branch. That is a repo-wide property of rebase-only
merging, not specific to the release path.

## Why the OrganizationAdmin bypass stays at `always`

All five rulesets in this repository also list `OrganizationAdmin` with
`bypass_mode: always`. That is a different exception from the App's, and it is
deliberate: it is the break-glass path back into `main` when the gates
themselves are what is broken — a wedged required check, a ruleset that matches
more than it should, a revert that has to land while CI cannot run.

Removing it is not free. With no admin bypass, repairing a misconfigured
ruleset means editing that ruleset in the GitHub UI before any fix can merge,
which is a slower and less reviewable path than the one being protected.

The control on it is convention, not configuration: the working agreement is
that `main` is a merge target only, and every change reaches it through a pull
request. That agreement holds in practice — of the twenty most recent commits
on `main`, twenty are associated with a merged pull request, bot-authored chart
bumps included:

```
$ for sha in $(git log -20 --format=%H origin/main); do \
    gh api "repos/jdwlabs/deployments/commits/$sha/pulls" --jq 'length'; done
```

If that ever stops being true, tighten the bypass to `pull_request` rather than
removing it. That still allows merging an emergency pull request with no
approving review — GitHub never lets an author approve their own — while
closing the direct push to `main`.
