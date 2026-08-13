# Agentic Concurrency Limits

Org policy is that uncapped agent automation is never acceptable (see
`platform/docs/adr/0015-agentic-contribution-identity-and-review-gates.md`,
"Concurrency and isolation"). This doc records the explicit cap for
`deployments` and the reasoning behind the number, not just the invariant
that a cap must exist.

## On ADR placement

`platform` is this org's ADR home (`docs/adr/`, version-controlled,
append-only per `platform/AGENTS.md`). `deployments` has no `docs/adr/`
directory and this doc recommends against creating one here: a second ADR
location per repo would fragment a decision record that already needs to be
read alongside ADR 0015, which is itself about all four repos, not just one.
If this cap (or its rationale) needs to graduate to a formal ADR, it belongs
in `platform/docs/adr/` as a follow-on to 0015, not as a new
`deployments/docs/adr/0001-...`. This file is the interim record for
`deployments` specifically until that happens.

## The cap

**Hard cap: 3 concurrent agentic actors per repo.**

**Status: documented policy, not an active control.** Nothing today
technically enforces this number — no launcher, queue, or CI gate refuses a
4th concurrent dispatch against this repo. This is the number an
orchestration layer should enforce once one exists (a launcher/queue
refusing a 4th concurrent agent dispatch against this repo), not a
self-discipline guideline agents are expected to police themselves. Until
that enforcement lands, the cap is upheld only by whoever is dispatching
agent sessions choosing to respect it.

"Agentic actor" here means an autonomous coding-agent session (e.g. a Claude
Code invocation) making judgment-driven changes in its own worktree, per the
"one worktree, one branch, one agent invocation" invariant. It does **not**
count `jdwlabs-release-bot`'s deterministic CI automation
(`promote-prd.yml`'s prd promotions, or the apps-repo `appVersion` bump
pipeline) — those are single-purpose, already serialized by their own
`concurrency:` groups (see below), and make no judgment calls a review needs
to weigh against other in-flight work. The cap targets the actors whose
concurrent output competes for the same reviewer's attention and the same
shared refs.

## Rationale

- **Single reviewer, not compute, is the bottleneck.** Every PR in this repo
  is reviewed by one person (`jdwillmsen`) per ADR 0015's "single-maintainer"
  framing. Adding agent concurrency doesn't add reviewer capacity — it only
  changes how many independent diffs are waiting for that one person at once.
  Past 3 concurrent, review queueing (not agent throughput) becomes the limit
  anyway, so a higher cap buys nothing but more collision surface.
- **Realistic human-initiated PR volume supports 3, not more.** Over the last
  30 days this repo saw ~37 human-authored PRs (`gh api
  repos/jdwlabs/deployments/pulls --paginate -f state=all`, filtered to
  non-bot authors) — about 1.2/day sustained. That's a track record of
  roughly one substantive human-directed task in flight at a time, with
  occasional overlap. 3 gives headroom for genuine parallel work (e.g. a
  chart bump + a CI fix + a docs change reviewed in one pass) without
  assuming a volume this repo has never actually sustained.
- **Collision hazards scale with actor count, not with task size.** Both
  documented hazards — the shared-worktree collision and the shared-ref race
  — get more likely as concurrent actors increase, even with the invariants
  and `concurrency:` groups in place as mitigations (they reduce the
  probability of a given collision, they don't make collisions
  actor-count-independent). Keeping the cap low keeps the blast radius of
  "the mitigations didn't catch this one" small.
- **This repo's blast radius is production.** Merging to `main` here deploys
  to the cluster (see `AGENTS.md`, "GitOps"). A cap that's comfortable for a
  low-stakes repo is not automatically comfortable here; 3 errs toward the
  conservative side deliberately.

## Revisit triggers

Revisit this number, not just re-affirm it, if any of the following happen:
review turnaround becomes the visible bottleneck at 3 concurrent actors
(cap is already binding and should probably drop, not rise); a second
qualified human reviewer starts approving PRs in this repo (changes the
"single reviewer" premise this rationale rests on); or JDWLABS-307 (GitHub
App identity) lands and makes per-agent attribution and per-actor throttling
enforceable in CI rather than only at the orchestration layer — at that
point the cap can be enforced as code instead of as documented policy.

## What this doc does not cover

Per-agent identity and attribution (who opened which PR) are out of scope
here — that's blocked on JDWLABS-307 (GitHub App identity), not yet landed.
This doc caps *how many* actors may run concurrently; it says nothing about
*which* actor a given PR came from.

## Related mechanisms already in force

- **Worktree exclusivity** — "one worktree, one branch, one agent
  invocation" (see `AGENTS.md`, "Concurrency"). Every actor counted against
  this cap needs its own worktree regardless of the cap; the cap bounds how
  many such worktrees may be active against this repo at once.
- **Shared-ref `concurrency:` groups** — `promote-prd.yml` (`group:
  promote-prd`) and, as of this change, `release.yml` / `update-pages.yml`
  (`group: gh-pages-write`) serialize writes to the refs those workflows
  share, so two concurrent runs can race in time but not in effect.

  **Known limitation, not fixed here:** `cancel-in-progress: false` holds
  exactly one pending run per group, not an unbounded queue. A 3rd
  concurrent entrant cancels the previously-pending run rather than queuing
  behind it, and a `cancelled` conclusion reads as success to anything
  matching only on `failure` — `prd-drift.yml` already documents this exact
  class of problem for `promote-prd.yml`'s group. For `gh-pages-write`, a
  batch of 3+ chart releases (or a release and an index update both pending
  at once) can silently drop a middle entry with no retry. Closing this
  fully needs either deeper per-group queuing than GitHub Actions offers, or
  `release-helm.yml` (`jdwlabs/.github`, upstream, out of scope for this
  repo) retrying/rebasing its push instead of a single non-retrying attempt
  — worth a follow-up ticket against `jdwlabs/.github` if this race is ever
  observed in practice rather than only reasoned about.
