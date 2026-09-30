# AGENTS.md

Canonical agent instructions for this repo; `CLAUDE.md` and `GEMINI.md` import
it. Layout, environments and adding an app: `README.md`.

This repo is the GitOps delivery layer for the jdwlabs tenant: merging to
`main` deploys. `platform` owns the cluster and the ApplicationSet that expands
`argocd/<env>/config.yaml` into Argo CD Applications (it also sets
`targetRevision`, so there is none here).

## Hard constraints

- Ask the user before `argocd app sync`, `kubectl apply` or `kubectl delete`;
  Git merge is the deploy path, so prefer a PR. Read-only
  `get`/`describe`/`logs`/`argocd app diff` are fine. (`.claude/settings.json`
  makes the mutating ones prompt.)
- NEVER hand-edit `charts/*/values-prd.yaml`: prd image pins change only via
  PRs opened by the `Promote PRD` workflow (`docs/prd-promotion.md`). Chart
  `version` is for chart packaging changes, never image tags.
- Every image reference in `charts/` is `<tag>@sha256:<index digest>` (the
  manifest-list digest, never a per-arch child) or has an entry with a reason
  in `tools/image-pin-allowlist.yaml`. `python3 tools/check-image-pins.py`
  enforces it in CI. A digest-only `tag: "@sha256:..."` is rejected because
  the common chart renders `repository:tag`, which is unpullable yet still
  passes `helm template`. The checker is vendored from `jdwlabs/.github`
  (CI fails on drift): change rules there and re-vendor; this repo's layout
  lives in `tools/image-pin-check.yaml`.
- No secret values: secrets come from Vault through ExternalSecret manifests.

## Charts

- App charts depend on `charts/common` via `file://../common`; run
  `helm dependency build charts/<name>` before `helm lint` / `helm template`.
- Render with `rtk proxy helm template <release> charts/<name> -f ...` — bare
  `helm template` is truncated by RTK even when redirected to a file.
- Bump `version` in `Chart.yaml` on any chart change.

## Required checks are coupled to rulesets

Required CI contexts live in `.github/rulesets/baseline.json` and are applied
to GitHub by hand (`apply.sh`) after merge. Renaming, merging or removing a CI
job needs the ordered procedure in `apply.sh`'s header, or every PR becomes
unmergeable. `.github/workflows/promote-prd.yml` hardcodes the same job names
in its generated PR body; update it in lockstep.

## Concurrency

Other agents work in this repo at the same time. Re-fetch `origin/main`
immediately before rebasing or pushing — a worktree's cached view goes stale
when a concurrent session pushes. The cap is 3 concurrent agentic actors
(policy, not yet enforced); cap rationale and worktree-exclusivity detail:
`docs/agentic-concurrency-limits.md`.

## Verify before acting

- Ticket evidence older than about a week, or from another investigation, is a
  hypothesis: re-check it against live state first.
- Before claiming something is absent, orphaned or drifted, state the scope
  searched ("all N apps"), not one sample.
- A disproved premise is a result: record it on the ticket.

## Tooling traps

`argocd login --core` namespace and Argo CD resource tracking vs
`managedFields`: `docs/tooling-traps.md`. Generic tool traps (RTK caching and
truncation — `gh pr view` can show a merged PR as `OPEN` — digests, `gh`,
Windows curl): `~/.local/share/chezmoi/docs/agent-tooling-traps.md` (dotfiles).
