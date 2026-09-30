# Tooling Traps

Argo CD symptoms that have misled agents and humans working in this repo, with
the cause and the fix. `AGENTS.md` points here. Traps in the generic tools
(`rtk` truncation and caching, `gh`, `kubectl` managed fields, index vs child
digests, `.imageID` vs `.image`, Windows `curl`) are box-wide:
`~/.local/share/chezmoi/docs/agent-tooling-traps.md` in the dotfiles repo.

| Symptom | Cause | Fix |
|---|---|---|
| Several SSA field managers on an object (e.g. `helm`, `argocd-controller`) make it look like ArgoCD doesn't track it, so it seems safe to leave un-pruned | Field managers (`managedFields`) answer *who set which field*; ArgoCD prune/ownership is decided by resource tracking. This install tracks by the `argocd.argoproj.io/tracking-id` **annotation** because annotation tracking is the Argo CD v3 default (server is v3.4.5) and `application.resourceTrackingMethod` — the key that actually selects the mechanism — is unset in `argocd-cm`. `application.instanceLabelKey` *is* set, but it only customises the label used when tracking is label-based, so it is inert here | Check the `tracking-id` annotation, not `managedFields`, when deciding whether ArgoCD tracks/will-prune a resource. If tracking behaviour ever looks wrong, read `application.resourceTrackingMethod` before assuming a default |
| `argocd app list` fails `argocd-cm not found` right after `argocd login --core` | `--core` needs no token — it authenticates from the kubeconfig — but it reads the target **namespace from the kube context**, not from where `argocd-cm` actually lives | Point the kube context at the `argocd` namespace (`kubectl config set-context --current --namespace=argocd`). There is no CLI flag for this: `-n`/`--namespace` are rejected as unknown flags, and `-N`/`--app-namespace` only filters which apps are listed |
