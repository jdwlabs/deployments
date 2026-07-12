# Contributing

## Commit Convention

This repository follows [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

### Types

| Type | When to use |
|------|-------------|
| `feat` | New service deployment or new chart |
| `fix` | Fix a broken deployment manifest or chart template |
| `build` | Chart dependency or tooling change |
| `chore` | Maintenance: version bumps, config cleanup |
| `ci` | CI/CD pipeline changes |
| `docs` | Documentation only |
| `perf` | Performance improvement |
| `refactor` | Restructure manifests/charts with no behavior change |
| `revert` | Reverting a previous commit |
| `style` | Formatting or whitespace only (no logic change) |
| `test` | Adding or updating tests |

### Format

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### Examples

```
feat(authui): add ArgoCD Application for authui service
fix(charts/porkbun): correct DNS challenge solver config
chore: bump usersrole chart to 0.4.2
docs: add troubleshooting section to README
ci: add helm lint to PR workflow
```

### Footers

Footers appear after an optional body, separated by a blank line. Common footers:

| Footer | When to use |
|--------|-------------|
| `Refs: JDWLABS-XX` | Links commit to a Jira issue (does not close it) |
| `Closes: JDWLABS-XX` | Closes the Jira issue on merge |
| `Closes: #N` | Closes a GitHub issue by number |
| `BREAKING CHANGE: <desc>` | Required when a chart change breaks existing deployments or removes a value |
| `Co-Authored-By: Name <email>` | Credit a co-author (human or AI) |

**AI contributor footer** — include when commits were written with AI assistance:

```
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

**Full examples with footers:**

```
feat(authui): add ArgoCD Application for authui service

Deploys authui to the jdwlabs namespace via the existing
frontend Helm chart. targetRevision set to HEAD.

Refs: JDWLABS-22
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

```
fix!(charts/frontend): rename replicas value to replicaCount

BREAKING CHANGE: values key renamed from replicas to replicaCount
to align with upstream chart conventions. Update all values.yaml
overrides before applying.

Closes: JDWLABS-80
```

### Rules

- Subject line ≤72 characters, lowercase, no trailing period
- Use imperative mood: "add" not "added" / "adds"
- Breaking changes: add `!` after type/scope and a `BREAKING CHANGE:` footer

## Pull Requests

1. Branch from `main`: `git checkout -b feat/short-description`
2. Run `helm lint` and `helm template` before opening PR
3. PR title must follow conventional commit format
4. Squash-merge to main — every merge = a deploy

## Development Setup

```bash
helm dependency build charts/<name>   # Fetch file:// library dependency
helm lint charts/<name>               # Validate chart
helm template test charts/<name>      # Render and review templates
argocd app diff <name>                # Preview diff vs live (read-only)
```
