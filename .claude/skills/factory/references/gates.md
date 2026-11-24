# Merge gates

Merge an implementation PR only when **every** gate below holds on the PR's **current** commit. Check the
mechanical gates yourself; a reviewer's summary is evidence, not proof. If a gate fails, don't merge: fix
it (if mechanical) or stop and ask (SKILL.md → "Stopping to ask").

| # | Gate | How to check |
|---|---|---|
| 1 | **CI green** | Bounded poll (below): every required check `pass`, none pending |
| 2 | **Up to date with `main`** | `gh pr view <PR> --json mergeStateStatus` isn't `BEHIND` or `DIRTY`. If it's `BEHIND`: `gh api -X PUT repos/<owner/repo>/pulls/<PR>/update-branch`, then re-check gate 1. `BLOCKED` alone just means "needs approval" and is covered by `--admin` |
| 3 | **No open blockers** | The Opus code review returned `READY`, or every blocker it listed was fixed by the builder **and** you verified each one with its reproduction on the current commit. Improvements don't block in the MVP phase |
| 4 | **Deviations disclosed and minor** | The PR body's "Deviations" plus the review's `undisclosed_deviations`. Scope, interface or dependency changes aren't minor |
| 5 | **No hard-rule violations** | Your own read of the diff against `CLAUDE.md`'s hard rules |
| 6 | **No workflow changes** | `gh pr diff <PR> --name-only` has nothing under `.github/`. Those go to the maintainer, in their own PR |
| 7 | **Dependency changes exactly as the plan names** | The `pyproject.toml` diff (`dependencies`, `optional-dependencies`, `version`) matches the plan; `uv.lock` changes only to match |
| 8 | **`CLAUDE.md` and `.claude/` unchanged** | `gh pr diff <PR> --name-only` |
| 9 | **Closes its issue** | The PR body has `Closes #<N>` |
| 10 | **You aren't unsure** | Skim the diff yourself. If something still bothers you, stop; don't merge |

## Bounded CI polling (gate 1)

Watch the **workflow run**, not the PR's checks. `gh pr checks --watch` follows individual checks, and
GitHub sometimes leaves one "pending" after its job has finished, which hangs the whole run. A run-level
watch ends when the run completes:

```bash
id=$(gh run list -R <owner/repo> --branch <branch> --workflow ci.yml --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$id" -R <owner/repo> --interval 15 --exit-status
gh run view "$id" -R <owner/repo> --json conclusion,jobs -q '.conclusion, (.jobs[] | "\(.name)=\(.conclusion)")'
```

Run it with a tool timeout (or in the background), not an open-ended wait. Don't use `sleep` loops: Claude
Code blocks foreground `sleep`, and macOS has no `timeout` command. If the run is `success` but a PR check
still shows pending, `gh run rerun <id>` clears it.

## Merge

```bash
gh pr merge <PR> --merge --admin --delete-branch
```

- `--admin` uses the maintainer's ruleset bypass (a PR's author can't approve their own PR).
- Use `--merge`, never `--squash` or `--rebase`, so release tags stay on `main`'s history.

Then post one comment on the PR: `Merged by the factory orchestrator: CI green; review READY (or N
blockers fixed and verified); no workflow/dependency/CLAUDE.md surprises; improvements → #M.` Log it,
`git -C <repo> pull --ff-only`, and `git worktree remove <worktree>`.

## After merging something that touches the factory itself

Open factory PRs keep running old workflows until their branches are updated with `main` (gate 2's
command). Update them before continuing.
