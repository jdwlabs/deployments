## What

<!-- One sentence: what changed and why -->

## Type of change

- [ ] `feat` — new service deployment or chart
- [ ] `fix` — broken manifest or chart template fix
- [ ] `build` — chart dependency or tooling change
- [ ] `chore` — maintenance / version bump / config
- [ ] `ci` — CI/CD pipeline change
- [ ] `docs` — documentation only
- [ ] `perf` — performance improvement
- [ ] `refactor` — restructure, no behavior change
- [ ] `revert` — revert a previous commit
- [ ] `style` — formatting / whitespace (no logic change)
- [ ] `test` — test additions or updates

## Checklist

- [ ] PR title follows conventional commit format: `type(scope): description`
- [ ] `helm lint charts/<name>` passes (if chart changed)
- [ ] `helm template` output reviewed (if chart changed)
- [ ] `argocd app diff` output checked (if application manifest changed)
- [ ] No secrets or credentials in diff
- [ ] `targetRevision` is `HEAD` or a semver tag (not a branch)
