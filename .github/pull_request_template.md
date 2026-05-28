## What

<!-- One sentence: what changed and why -->

## Type of change

- [ ] `feat` — new service deployment or chart
- [ ] `fix` — broken manifest or chart template fix
- [ ] `chore` — maintenance / version bump
- [ ] `docs` — documentation only
- [ ] `ci` — CI/CD pipeline change
- [ ] `refactor` — restructure, no behavior change

## Checklist

- [ ] PR title follows conventional commit format: `type(scope): description`
- [ ] `helm lint charts/<name>` passes (if chart changed)
- [ ] `helm template` output reviewed (if chart changed)
- [ ] `argocd app diff` output checked (if application manifest changed)
- [ ] No secrets or credentials in diff
- [ ] `targetRevision` is `HEAD` or a semver tag (not a branch)
