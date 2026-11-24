---
name: factory
description: >-
  Run this repo's agent factory end to end from Claude Code. Brainstorm a feature or roadmap with the
  maintainer and turn it into detailed, dependency-linked GitHub issues; then take each ready issue from
  plan to merged PR with a Sonnet builder and a fresh Opus reviewer, fixing only what blocks the main path
  and filing the rest as improvements. Questions for the maintainer go on the PR as inline comments while
  the run continues; "resume" picks up their answers. Use this whenever the user wants to plan or break
  down work into issues, "run the factory", "resume the factory", work through open issues, pick up issue
  #N, go from idea to merged PRs, or says the label-by-label factory is too slow, even if they don't name
  the skill.
---

# Factory

This repo is built by an issue-driven agent factory (see `README.md` → "How this repo is built" and
`CLAUDE.md`). This skill runs it from a Claude Code session. You (Opus) are the orchestrator: a Sonnet
**builder** plans and codes, a fresh Opus **reviewer** checks the result, and you merge when the gates
pass. The maintainer answers questions **asynchronously on the PR**; the run never sits waiting.

Three principles shape everything below. All of them came out of real runs:

1. **Urgency over completeness.** Before the project's first release (`Release phase: mvp` in
   `CLAUDE.md`), the goal is a main path that works end to end, then iteration. Only **blockers** are
   fixed inside a PR; everything else becomes an `improvement` issue.
2. **Don't pay for context twice.** The agent that plans is the agent that builds and fixes
   (`SendMessage`). Only reviewers are fresh, once per issue. When an agent has to be replaced (a new
   session), it starts from the PR's **Handoff**, not from scratch.
3. **Flag and continue.** When the maintainer must decide something, put the question on the PR, label
   it, and move on to other work. Never block the run waiting for an answer, unless nothing else can
   proceed.

## Where knowledge lives

| What | Where | Why |
|---|---|---|
| Decisions, plans, handoffs, review threads, status, lessons | **GitHub and the repo**: issues, PRs, labels, `.factory/designs/issue-N.md`, `CLAUDE.md` | Shared, durable, visible to the maintainer and every agent. **The source of truth.** |
| Progress and cost of a run | **The run log** (`.factory/runs/*.jsonl`, gitignored, local) | A cache and cost ledger. It **points** at GitHub (PR, commit, thread ids) and never copies content. Delete it and nothing breaks. |
| The maintainer's preferences, where to look | **Claude's memory** (`~/.claude/projects/…/memory`) | Private, local. If a preference is really a project rule, it belongs in `CLAUDE.md`. |

When the run log or memory disagrees with GitHub, GitHub wins.

## Before you start

1. **Model.** You should be on Opus. If not, tell the user before orchestrating.
2. **Repo state.** `gh auth status` is OK, `main` is clean and up to date, and the `CLAUDE.md` checks pass
   on `main`. If `main` is red, stop.
3. **Read** `CLAUDE.md` (context, "Release phase", "Definition of checks pass", hard rules, "Where
   knowledge lives") and the stage prompts in `.claude/commands/`.
4. **Repo slug.** `gh repo view --json nameWithOwner -q .nameWithOwner`. Paste that exact value into
   every brief; never type it from memory.
5. **What's waiting.** Run `python .claude/skills/factory/scripts/queue.py`. It shows issues in progress,
   **issues waiting on the maintainer and whether they've replied**, and the ready queue.
   `runlog.py status` shows the local log's view. If anything is in progress or has new replies, handle
   that first (see "Resume").
6. **Long runs.** Your own context grows with every step. After 2–3 issues, or when the session feels
   long, finish the current step, write the run summary and suggest a fresh session. GitHub and the run
   log let it pick up cleanly.

## Mode 1: Brainstorm → issues

1. **Brainstorm with the user yourself** (Opus, main session): goals, users, constraints, out of scope,
   how they'll know it works. Read the code it touches. Identify the **main path**, the smallest chain of
   issues that makes the thing work end to end, and order it first.
2. **Propose a breakdown table:** slug, title, one-line goal, dependencies (main-path only; never make a
   main-path issue wait on a polish issue), areas, size (S/M; split anything L), **risk tier** (see below).
   Mark polish and hardening items as `improvement`.
3. **Get one confirmation** ("create these N issues?"), because issues are visible to others. This is a
   live conversation, so asking in the session is right here.
4. **Draft bodies.** For several issues, spawn one Sonnet "Issue writer" per issue in parallel
   (`references/briefs.md`). For one or two small issues, write them yourself.
5. **Check and create, in dependency order** (`references/issue-format.md`), replacing `{{slug}}` with
   real `#N`. Apply the `improvement` label where it belongs.

## Mode 2: Run the factory

### Pick the work

`queue.py` lists, in order: issues in progress, issues **waiting on the maintainer with new replies**
(resume those first), ready main-path issues, then ready `improvement` issues. **At most two issues in
flight; merges are always one at a time.** Issues waiting on the maintainer without new replies don't
count as in flight. Leave them, and leave anything that depends on them.

### Risk tiers

| Tier | When | Subagents |
|---|---|---|
| **Standard** | most S/M issues | builder (plans, builds, fixes) + 1 Opus code reviewer = **2** |
| **Critical** | privacy or security code; a new external dependency; a data contract other issues build on | + 1 Opus **design reviewer** between plan and build = **3** |

### For each issue N

Create a worktree on a **`local/issue-N`** branch: `git worktree add ../<repo>-issue-N -b local/issue-N
origin/main`. The `local/` prefix keeps the GitHub Actions factory (which reviews `factory/issue-*`
branches) from also reviewing skill PRs. Pass subagents **absolute paths**. Set `factory:designing`. Log
each step with `runlog.py add`.

**1. Plan (Sonnet builder, phase 1).** Spawn the builder with the "Builder" brief, phase 1: it writes
`.factory/designs/issue-N.md`, commits on `local/issue-N`, pushes, opens a **draft PR** ("feat: … (#N)",
`Closes #N`) and **stops**, returning the plan. Keep the agent's id: the same agent builds and fixes.

**2. Plan check.**
- **Standard tier:** you skim the plan yourself. Does it meet the acceptance criteria and fit the code?
  Does it add scope the issue didn't ask for? Does it rest on an unverifiable assumption about real data
  (see "Reality check")? Send corrections to the builder (`SendMessage`).
- **Critical tier:** spawn a fresh Opus "Design reviewer". Send `REVISE` changes to the builder; you check
  they landed (no second review). Reviewer `notes` go to the builder as guidance.
- **If the plan needs a maintainer decision** (a product or priority call, or an unverifiable assumption):
  **flag and continue** (below), with the question anchored on the relevant line of the plan file in the
  draft PR. Don't build until it's answered.
- Otherwise record the approval as a PR comment. It replaces the human `design:approved` label; never add
  that label, because it starts the GitHub Actions build. Set `factory:building`.

**3. Build (same builder, phase 2).** `SendMessage` the builder: "plan approved, build it" (plus notes). It
implements, runs every `CLAUDE.md` check until green, fills in the PR body (**Handoff**, "Checks run",
"Deviations"), marks the PR ready for review and sets `factory:review`. Verify all of that, especially
that the Handoff section exists.

**4. Review ∥ CI.** Start the fresh Opus "Code reviewer" **while CI runs**. It posts **one review with
inline, line-anchored comments**, each tagged `blocker`, `improvement` or `question`. Wait on CI with a
bounded run-level watch (`references/gates.md` → gate 1).

**5. Fix blockers (same builder).** `SendMessage` the blocker list, with reproductions. The builder fixes
them, adds a failing-without-the-fix test for each, re-runs the checks, pushes, **updates the Handoff**, and
replies on each blocker thread saying what changed. You verify each blocker with its reproduction plus
green CI (no re-review) and **resolve its thread**. Allow two fix rounds; if blockers persist, flag and
continue.

**6. Questions.** If the review raised `question`s, or you need the maintainer to decide anything: **flag
and continue**. Otherwise go on.

**7. Improvements.** Write **one** `improvement` issue yourself listing the non-blocking findings (with
links to their review threads), `Depends on #N`, labelled `improvement`. Then resolve those threads with a
reply pointing at the issue. Never make a main-path issue depend on it.

**8. Merge.** Check every gate in `references/gates.md` on the PR's current commit, **including "all
review threads resolved"**. Append the Handoff's gist to `.factory/designs/issue-N.md` (one commit on the
branch, so the rationale lands on `main` next to the code). Then `gh pr merge <PR> --merge --admin
--delete-branch`, post the merge note, log it, pull `main`, remove the worktree, and run `queue.py` again.

### Flag and continue

When the maintainer has to decide something, don't wait in the session:

1. Make sure the question is **on the PR, anchored to the line it's about**: an inline review comment
   (the reviewer's `question`, or your own via the API in `references/gates.md` → "Posting questions").
   Each question states **the options and your recommendation**, so a one-word reply is enough. Questions
   that aren't about one line go in a single PR comment.
2. Add `factory:needs-maintainer` to the **issue and the PR**. Remove the in-progress label.
3. Log a `needs-maintainer` step with the thread ids in `refs`.
4. **Move on** to the next independent ready issue. Anything that depends on this issue waits.

Ask in the session (`AskUserQuestion`) **only** if nothing else can proceed and the maintainer is clearly
present. Even then, put the question and the answer on the PR too, so the record is complete.

These go to the maintainer this way:
- a design reviewer's `ASK`, or a plan that needs a product or priority decision;
- a reviewer's `question`, or a reviewer below high confidence on a blocker;
- blockers that remain after two fix rounds, or checks that can't go green;
- a gate failure (workflows, dependencies the plan didn't name, version, `CLAUDE.md`);
- two consecutive rounds with blockers in the same area (the premise may be wrong);
- an unverifiable assumption about real data (see "Reality check");
- a subagent that added `factory:blocked`.

### Resume

"Resume the factory", or the start of any run where `queue.py` shows replies. **Work from GitHub state**;
the run log only speeds this up.

1. For each PR labelled `factory:needs-maintainer` **with new maintainer replies**: read its open threads,
   the replies, the PR's **Handoff** and the diff.
2. Decide how to apply each answer:
   - **A small, clear edit** (roughly under 20 lines, no plan change): apply it yourself, run the
     `CLAUDE.md` checks, push, and reply on the thread.
   - **A real code change:** spawn a **resume builder** (`references/briefs.md`) with the Handoff, the diff,
     the threads and only the files the Handoff names. It reuses no earlier context, so the Handoff is its
     starting point.
   - **A decision that changes the plan:** update `.factory/designs/issue-N.md` first, then as above.
   - **"Do it your way"** or an approved recommendation: apply the recommendation.
3. **Check it.** CI is green, and each answered thread is addressed: you verify small edits yourself. For a
   builder's changes, run a fresh Opus code reviewer **scoped to those threads and the diff since the
   question** (the "Code reviewer" brief with the resume scope).
4. Resolve the addressed threads, remove `factory:needs-maintainer`, set `factory:review`, log `resume`,
   and continue from step 7 (improvements, then merge).

If the maintainer used `@claude` on the PR (the Actions `factory-chat` workflow), its commits are already
there: treat them like a builder's changes and check them.

### Overlapping issues

While issue A builds, waits for CI, or waits on the maintainer, you may start the **plan** of an
independent ready issue B (no dependency either way, and no files A is changing), in its own worktree with
its own builder. Keep at most two issues **actively in flight**, and merge one at a time. After A merges,
update B's branch with `main` (`gh api -X PUT repos/<owner/repo>/pulls/<PR>/update-branch`) before B's
review.

### Reality check

Agents may not read real user data (`CLAUDE.md`), so they can only *assume* how external formats look. When
a plan or review rests on an assumption about real data that the code, the issue or a cited spec can't
verify (a file layout, a field that "probably" exists), **don't build on it.** Flag it as a question:
ideally the maintainer runs a local script that prints only the **shape** of their data (record types,
field names, counts), never values, and replies with the output.

## Coexisting with the GitHub Actions factory

The two modes never trigger each other by accident: skill branches are `local/issue-N` (the Actions review
only matches `factory/issue-*`), and the skill never adds `factory:design` or `design:approved` or writes
`@claude`. The maintainer **may** write `@claude` on a skill PR on purpose, to have `factory-chat` apply an
answer right away; resume handles it.

## When you're done

`python .claude/skills/factory/scripts/runlog.py summary` prints per-issue wall time, rounds and tokens by
model and role. Report:
- issues merged (PR links);
- **issues waiting on the maintainer**, with links to their open threads;
- improvements filed;
- decisions made on the maintainer's behalf;
- the cost summary.

If a problem was a factory problem rather than a project problem, say so: it belongs in the factory
template as a `lesson` issue.

## Reference files

- `references/issue-format.md`: the Factory-task issue body and its quality bar.
- `references/briefs.md`: prompts for every subagent, the builder's follow-up messages, and the Handoff
  format.
- `references/gates.md`: merge gates (including all threads resolved), bounded CI waits, posting and
  resolving review threads, and the merge command.
- `scripts/queue.py`: the queue (in progress, waiting on the maintainer and replied, ready; main path
  first).
- `scripts/runlog.py`: the run log (`add`, `status`, `summary`).
