# AGENTS.md

Canonical context for AI agents (Claude Code, OpenAI Codex, Gemini CLI, GitHub Copilot, and others) working in this repository. `CLAUDE.md` and `GEMINI.md` are thin pointers to this file — make edits here.

## What This Repo Is

jdwlabs `deployments` defines what runs on the jdwlabs Kubernetes cluster. It contains per-environment app definitions and custom Helm charts for tenant services. It is the GitOps delivery layer — `platform` configures the cluster foundation, this repo defines the workloads.

## Repository Structure

- `argocd/<env>/config.yaml` — app definitions for that environment (`non`, `prd`), expanded into ArgoCD Applications by the platform ApplicationSet
- `charts/<name>/` — custom Helm charts for jdwlabs services (`values.yaml` base + `values-<env>.yaml` overrides)
- `charts/common/` — shared library chart consumed by every app chart
- `tools/` — repo tooling: `generate-index.py` renders the Helm chart index page pushed to GitHub Pages by CI; `check-image-pins.py` fails CI on any mutable image reference (unit tests alongside it in `test_check_image_pins.py`); `check-prd-drift.py` grades how far each chart's prd pin trails its released `appVersion` for the `prd Drift` schedule; `notify-alert.py` posts one Alertmanager alert so a scheduled workflow's result reaches the AI-SRE relay (warning) or Discord as well (critical) instead of stopping at the Actions tab

## Key Concepts

- **GitOps:** This repo is the source of truth for what is deployed. Merging to `main` = deploying to production. Never bypass this with direct kubectl/argocd apply.
- **ArgoCD Application CRs:** Each app entry in `argocd/<env>/config.yaml` describes an application ArgoCD should deploy, including source chart, target namespace, and values.
- **Helm charts in `charts/`:** Custom charts for services that don't have an upstream chart or need heavy customization. App charts consume the `common` library chart (`charts/common/`) for the shared workload scaffold via a `file://../common` dependency — run `helm dependency build charts/<name>` before `helm lint`/`helm template`.
- **ExternalSecrets:** All secrets are injected at runtime from Vault via ExternalSecrets Operator — this repo contains no secret values.

## Development Commands

### Validate Helm charts

```bash
helm dependency build charts/<chart-name>        # Fetch the file:// library dependency (needed once before lint/template)
helm lint charts/<chart-name>                    # Lint chart for errors
rtk proxy helm template <release> charts/<chart-name>          # Render templates locally
rtk proxy helm template <release> charts/<chart-name> --debug  # Verbose render with values
```

`helm template` is run through `rtk proxy` on purpose — a bare `helm template` is
truncated by RTK even when redirected to a file (see Tooling Traps).

### Inspect ArgoCD state (read-only)

```bash
argocd app get <app-name>                        # App health and sync status
argocd app diff <app-name>                       # Live vs desired state diff
argocd app list                                  # All apps and their status
```

These need the kube context pointed at the `argocd` namespace after
`argocd login --core`, otherwise they fail `argocd-cm not found` (see Tooling
Traps) — there is no namespace flag on the argocd CLI to work around it.

### Inspect Kubernetes state (read-only)

```bash
kubectl get applications -n argocd               # All ArgoCD Application CRs
kubectl describe application <name> -n argocd    # Detailed app status and events
kubectl get pods -n <namespace>                  # Pod status for a namespace
kubectl logs <pod> -n <namespace>                # Pod logs
```

### Branch protection / required checks

Branch rulesets (required status checks, review rules) are managed as code
in [`.github/rulesets/`](.github/rulesets/) and applied to GitHub manually
via `apply.sh` after merge — see that script's header comment before
renaming, merging, or removing any required CI job context; doing it in the
wrong order can make a PR permanently unmergeable or block every open PR.
`.github/workflows/promote-prd.yml` also hardcodes required-check job names
into a generated PR body and must be updated in the same lockstep.

## Common Tasks

### Add a new application deployment

1. Create the Helm chart under `charts/<chart-name>/` if it's a custom chart — consume the `common` library chart (see `charts/common/README.md`)
2. Run `helm dependency build charts/<chart-name>` then `helm lint charts/<chart-name>` to validate
3. Add an entry to each relevant `argocd/<env>/config.yaml` (`name`, `namespace`, `chartPath`, `valueFiles`)
4. Merge to main — ArgoCD picks it up automatically

### Debug a failed ArgoCD sync

1. `argocd app get <name>` — check health and sync status
2. `argocd app diff <name>` — see what has drifted from desired state
3. `kubectl describe application <name> -n argocd` — detailed events
4. Check ArgoCD UI for full error output

### Update a chart

1. Edit the chart under `charts/<chart-name>/` and bump `version` in its `Chart.yaml`
2. Adjust `values.yaml` / `values-<env>.yaml` as needed
3. Run `rtk proxy helm template` to verify the rendered output looks correct
4. Merge to main — ArgoCD will sync automatically

## Code & Manifest Comments

Never put a Jira ticket ID (`JDWLABS-*`) or PR/issue number in a comment in
any file here — app config, ArgoCD manifests, and Helm charts included.
Traceability lives in the commit message and PR description; comments
should explain *why* the config is what it is so they stay meaningful
after the ticket closes.

## Concurrency: one worktree, one branch, one agent invocation

Multiple AI agents may operate against this repo at the same time. Never
work on `main`/`master`, and create a worktree before touching code. For
humans this is standing practice; for agents it is a **hard invariant,
not a convention they can relax**:

- Every agent invocation gets its own worktree and its own branch. Never
  share a worktree across two concurrent agent sessions, and never reuse
  one worktree for a second, unrelated task after the first is done —
  create a fresh one instead.
- Before rebasing or pushing, re-fetch `origin/main` rather than trusting
  the worktree's cached view of it. A worktree that looks up to date can
  be stale by the time a concurrent session has pushed.
- Never assume you are the only agent with a checkout of this repo. Two
  sessions sharing state is how an unpushed local commit has landed on
  `main` minutes after a second, unrelated session already pushed — the
  failure is silent until the histories are compared.
- The cap on concurrent agentic actors against this repo is 3. This is
  documented policy today, not an active control — no launcher or queue
  enforces it yet, so it will be enforced at the orchestration layer once
  one exists. See `docs/agentic-concurrency-limits.md` for the cap and its
  rationale.

This generalizes the same failure mode `.github/workflows/promote-prd.yml`
already had to solve for a single automated actor: a `concurrency:` group
serializes runs so two can never race on the same target ref
(`release.yml` and `update-pages.yml` carry the same pattern, scoped to the
shared `gh-pages` ref both write to). A shared mutable resource (a
worktree, or a branch, or a ref two workflows both push) needs exclusive
ownership per in-flight task, or concurrent writers eventually race. See
`platform/docs/adr/0015-agentic-contribution-identity-and-review-gates.md`
("Concurrency and isolation") for the fuller rationale — that ADR is still
`proposed`; this invariant is the part of it already in force.

## Tooling Traps

RTK's filtered output is **not** the tool's output — it summarises, truncates, and
prints its own status lines. Every `rtk` row below is that one root cause. Run
anything you intend to act on through `rtk proxy <cmd>` and read the raw result.

| Symptom | Cause | Fix |
|---|---|---|
| `rtk helm template` output looks complete but is truncated — even redirected to a file | RTK caps captured output regardless of the redirect target | `rtk proxy helm template ...` for the untruncated render |
| A resource looks unowned/unmanaged in `kubectl get -o json` | `managedFields` is hidden by default | Add `--show-managed-fields` |
| Several SSA field managers on an object (e.g. `helm`, `argocd-controller`) make it look like ArgoCD doesn't track it, so it seems safe to leave un-pruned | Field managers (`managedFields`) answer *who set which field*; ArgoCD prune/ownership is decided by resource tracking. This install tracks by the `argocd.argoproj.io/tracking-id` **annotation** because annotation tracking is the Argo CD v3 default (server is v3.4.5) and `application.resourceTrackingMethod` — the key that actually selects the mechanism — is unset in `argocd-cm`. `application.instanceLabelKey` *is* set, but it only customises the label used when tracking is label-based, so it is inert here | Check the `tracking-id` annotation, not `managedFields`, when deciding whether ArgoCD tracks/will-prune a resource. If tracking behaviour ever looks wrong, read `application.resourceTrackingMethod` before assuming a default |
| `argocd app list` fails `argocd-cm not found` right after `argocd login --core` | `--core` needs no token — it authenticates from the kubeconfig — but it reads the target **namespace from the kube context**, not from where `argocd-cm` actually lives | Point the kube context at the `argocd` namespace (`kubectl config set-context --current --namespace=argocd`). There is no CLI flag for this: `-n`/`--namespace` are rejected as unknown flags, and `-N`/`--app-namespace` only filters which apps are listed |
| A `Promote PRD` dispatch is assumed to write a bare, mutable tag into `values-prd.yaml`, so every dispatch is hand-fed `-f tag=<version>@sha256:<digest>` | Obsolete — the workflow resolves the tag itself now. `${INPUT_TAG:-appVersion}` goes through `pin_reference`, which turns a bare tag into `<tag>@sha256:<index digest>` via `tools/resolve-image-digest.sh` and **fails the run** when it cannot, and a second pass re-rejects any candidate without `@sha256:` before a PR is opened. A bare dispatch can no longer produce a bare pin | Dispatch the bare tag, or omit `-f tag=` entirely, and let the workflow resolve the index digest. Reserve `-f tag=<tag>@sha256:<digest>` for a deliberate rollback to a digest the tag no longer points at: that form is used **verbatim** and never re-resolved, so a per-arch **child** digest supplied by hand bypasses the index-only guard and reaches prd unchecked |
| Comparing a registry digest for an image tag against a pod's `imageID` reports drift that isn't real | A tag can be an OCI **index** (multi-arch manifest list); its digest is never equal to one of its own per-platform **child manifest** digests, even for byte-identical content | Resolve both sides to the same manifest level before comparing (`rtk proxy docker buildx imagetools inspect <ref>`) |
| `imageID` **may** report a different repo name than the pod spec's image | containerd dedups pulled content by config digest and can report it under whichever reference resolved first. *Not reproduced: a 20-pod sample across 6 namespaces found neither a counterexample nor a refutation — treat this as a possibility to rule out, not established behaviour* | If repo names do disagree, compare config/layer digests rather than concluding the wrong image is running |
| `gh pr view <n>` reports `OPEN` for a PR that has already been merged | RTK caches the `gh` response, and the cached body is well-formed — unlike the truncated render above, a stale answer gives you nothing to notice. Observed on three PRs at once: `gh pr view` said `OPEN` while all three were already merged. The same staleness reaches the check summary, so a red gate can read green | `rtk proxy gh pr view <n>` (or `rtk proxy gh pr list`) returns live state. Via the API read `.merged`, not `.state` — REST only reports `open`/`closed`, so a merged PR reads `closed`: `gh api repos/<owner>/<repo>/pulls/<n> --jq .merged` |
| `gh pr edit` fails on every PR in this org | `gh` resolves the org through a GraphQL **query** that requires the `read:org` scope, and the active `GITHUB_TOKEN` (`ghp_...`) lacks it — it fails before any mutation is attempted (`the 'login' field requires ... ['read:org']`) | `unset GITHUB_TOKEN` so `gh` falls back to the keyring `gho_` OAuth token, which already carries `read:org`. Fallback if that token is unavailable: `gh api -X PATCH repos/<owner>/<repo>/pulls/<n> --input payload.json` |
| `gh run watch <n>` errors or watches nothing | It takes the run's **databaseId**, not the run number shown in the UI or in a `gh run list` number column | Resolve it first — `gh run list --json databaseId,number,headBranch` — and pass the `databaseId` |
| `.status.containerStatuses[].image` disagrees with the pod spec — a bare `sha256:…` with no repo, or a digest that matches nothing you deployed | That field carries the **config** digest, reported under whichever reference resolved first; `.imageID` carries the repo plus the **manifest** digest. Sampled live: `.image` was `sha256:9700374b…` with no repo while `.imageID` was `docker.io/jdwlabs/ai-sre-relay@sha256:f42b749b…` — two different digests for one running container | Read `.imageID`, never `.status…image`, when verifying which image is running. If the repo names still disagree, compare config/layer digests rather than concluding the wrong image is deployed |
| `curl --cacert <ca>.pem https://host` reports HTTP 000 on Windows, and it is read as "`--cacert` was ignored" | Windows curl uses the **Schannel** TLS backend, which **does** honour `--cacert` — it verifies against that bundle alone and fails loudly when the chain does not build. HTTP 000 only means no HTTP response was received; every TLS failure reports it, so the status carries no diagnostic information at all. Verified on curl 8.21.0 (Windows, Schannel): the same `--cacert isrgrootx1.pem` fails `https://example.com` and succeeds against `https://letsencrypt.org`, which is only possible if the bundle is being applied | Read the **exit code**, never the HTTP status: `60` = chain did not verify against the supplied bundle (wrong CA, or an empty one), `77` = bundle unreadable or holding no extractable certificate, `7` = connection refused before TLS, `2` = the `--cacert` path does not exist. No `openssl s_client` detour is needed |

## Verify Before You Start

Ticket evidence more than ~a week old (or gathered in a different investigation) is a hypothesis, not ground truth. Before acting on it:

- Re-confirm the ticket's premises against live state — don't build on a stale finding
- State the scope you searched before claiming something is absent, orphaned, or drifted ("checked all N apps", "every pod in the release") — one sample is not the whole set
- A disproved premise is a valuable result: record it on the ticket, don't quietly work around it

## Constraints

- `argocd app sync` is NEVER run autonomously — sync is triggered by Git merge
- NEVER hand-edit `charts/*/values-prd.yaml` — prd image tags change only via
  pull requests opened by the `Promote PRD` workflow (see `docs/prd-promotion.md`);
  `Chart.yaml` `version` is for chart packaging changes only, never image tags
- `kubectl apply` and `kubectl delete` are out of scope — workload management is ArgoCD's job
- Read-only `kubectl get`, `kubectl describe`, `kubectl logs` are safe
- `targetRevision` must always be `HEAD` or a semver tag — never a branch name other than the default
- Every image reference in `charts/` must be digest-pinned as `<tag>@sha256:<index digest>` — the **index** (manifest-list) digest, never a per-arch child manifest digest — or carry an entry in `tools/image-pin-allowlist.yaml` keyed on path + repository + tag with an inline reason. Run `python3 tools/check-image-pins.py` before pushing; a digest-only tag (`tag: "@sha256:..."`) is rejected because the common chart concatenates `repository:tag` and would render something unpullable that `helm template` still accepts
- No secrets in this repo — reference Vault paths via ExternalSecret manifests only

## References

- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [Helm Documentation](https://helm.sh/docs/)
