# Merge gates

Merge an implementation PR only when **every** gate below holds on the PR's **current** commit. Check the
mechanical gates yourself; a reviewer's summary is evidence, not proof. If a gate fails, don't merge: fix
it if it's mechanical, otherwise **flag and continue** (SKILL.md).

| # | Gate | How to check |
|---|---|---|
| 1 | **CI green** | Run-level watch (below): every required check `pass`, none pending |
| 2 | **Up to date with `main`** | `gh pr view <PR> --json mergeStateStatus` isn't `BEHIND` or `DIRTY`. If it's `BEHIND`: `gh api -X PUT repos/<owner/repo>/pulls/<PR>/update-branch`, then re-check gate 1. `BLOCKED` alone just means "needs approval": the merge needs the maintainer's bypass or approval (see "Merge") |
| 3 | **No open blockers** | The Opus code review returned `READY`, or every blocker was fixed by the builder **and** you verified each one with its reproduction on the current commit. Improvements don't block in the mvp phase |
| 4 | **Deviations disclosed and minor** | The PR body's "Deviations" plus the review's `undisclosed_deviations`. Scope, interface or dependency changes aren't minor |
| 5 | **No hard-rule violations** | Your own read of the diff against `CLAUDE.md`'s hard rules |
| 6 | **No workflow changes** | `gh pr diff <PR> --name-only` has nothing under `.github/`. Those go to the maintainer, in their own PR |
| 7 | **Dependency changes exactly as the plan names** | The `pyproject.toml` diff (`dependencies`, `optional-dependencies`, `version`) matches the plan; `uv.lock` changes only to match |
| 8 | **`CLAUDE.md` and `.claude/` unchanged** | `gh pr diff <PR> --name-only` |
| 9 | **Closes its issue** | The PR body has `Closes #<N>` |
| 10 | **Handoff present** | The PR body has an up-to-date "## Handoff" section |
| 11 | **All review threads resolved** | The unresolved-thread query (below) returns 0. No maintainer question is left open, and every blocker thread was verified and resolved |
| 12 | **Docs ship with the code** | The Handoff has a "**Docs:**" line naming the docs updated, or "none needed" with a reason. If `gh pr diff <PR> --name-only` changes code but no docs (README, CHANGELOG, `docs/`, docstrings, `.factory/designs/` beyond the plan), that line must say why none were needed, and the reason must hold up against the diff. Docs that disagree with the code are a hard-rule blocker (gate 5) |
| 13 | **You aren't unsure** | Skim the diff yourself. If something still bothers you, flag it on the PR instead of merging |

## Bounded CI waits (gate 1)

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

## Posting questions (and other review comments) inline

One review, many line-anchored comments. Write the payload to a temp file, then:

```bash
cat > /tmp/review.json <<'JSON'
{"event": "COMMENT",
 "body": "Summary: 1 question, 2 improvements.",
 "comments": [
   {"path": "pkg/writer.py", "line": 42, "side": "RIGHT",
    "body": "[question] Should `--out` default to `./traces` or require an explicit path?\nOptions: (a) default ./traces; (b) required.\nRecommendation: (a), matching the README example."}
 ]}
JSON
gh api -X POST repos/<owner/repo>/pulls/<PR>/reviews --input /tmp/review.json
```

`line` must fall inside the PR's diff. If GitHub rejects a comment (HTTP 422), anchor it to the nearest
changed line, or put that question in a plain PR comment (`gh pr comment <PR> --body-file …`). For questions
about the plan before any code exists, anchor them on `.factory/designs/issue-<N>.md` in the draft PR.

Reply on a thread: `gh api -X POST repos/<owner/repo>/pulls/<PR>/comments/<comment-id>/replies -f body="…"`.

## Finding and resolving threads (gate 11, and resume)

```bash
gh api graphql -f query='
query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){ pullRequest(number:$n){
  reviewThreads(first:100){ nodes{ id isResolved path line
    comments(first:20){ nodes{ databaseId author{login} createdAt body } } } } } } }' \
  -f o=<owner> -f r=<repo> -F n=<PR> \
  -q '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved|not)'
```

- **Unresolved count** (gate 11): the same query piped to `| length`, or count the objects returned.
- **New maintainer replies** (resume): a thread whose **last comment's author** is the maintainer is
  waiting on you. `queue.py` does this check for you.
- **Resolve** a thread after verifying it's addressed:
  `gh api graphql -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}' -f id=<thread-node-id>`

Resolve only what you've verified: a blocker's fix (repro plus CI), an answered question that's been
implemented, an improvement filed as an issue, or a `doc-debt` finding filed as a sub-issue of the
"Documentation tech-debt" issue (reply with the link first).

## Merge

Before merging, append the Handoff's gist to `.factory/designs/issue-<N>.md` under a final
"## Implementation notes" heading (what was built and why, decisions from review threads, follow-up
issues), commit it on the branch ("docs: implementation notes (#<N>)"), push, and let CI pass. Then:

Pick the command from `gh pr view <PR> --json mergeStateStatus`:

```bash
gh pr merge <PR> --merge --delete-branch           # CLEAN
gh pr merge <PR> --merge --admin --delete-branch   # BLOCKED only by the required review
```

- `--admin` uses the maintainer's ruleset bypass (a PR's author can't approve their own PR). Add it only
  when the PR is blocked solely by the required review.
- Use `--merge`, never `--squash` or `--rebase`, so release tags stay on `main`'s history.
- **If the merge is refused** (the harness denies the command, or GitHub rejects it), **stop**. Never retry
  through `gh api .../pulls/<PR>/merge`, other flags or another tool. Hand it off (SKILL.md → "Ready to
  merge and continue"): add `factory:ready-to-merge` to the PR and issue, remove the in-progress label, and
  post one PR comment in this shape, with the command that fits the PR's state:

  ````
  Ready to merge: all gates pass on <sha>.

  ```bash
  gh pr merge <PR> --merge [--admin] --delete-branch -R <owner/repo>
  ```

  CI green; review READY …; all threads resolved; docs updated (…); improvements → #M; doc-debt → #D…
  ````

Once it has merged, post one comment on the PR: `Merged by the factory orchestrator: CI green; review READY (or N blockers
fixed and verified); all threads resolved; docs updated (<list> or "none needed: <reason>"); no
workflow/dependency/CLAUDE.md surprises; improvements → #M; doc-debt → #D… (or "none").`
Log it, `git -C <repo> pull --ff-only`, and `git worktree remove <worktree>`.

## After merging something that touches the factory itself

Open factory PRs keep running old workflows until their branches are updated with `main` (gate 2's
command). Update them before continuing.
