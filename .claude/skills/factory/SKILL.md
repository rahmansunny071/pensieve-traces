---
name: factory
description: >-
  Run this repo's agent factory end to end from Claude Code. Brainstorm a feature or roadmap with the
  maintainer and turn it into detailed, dependency-linked GitHub issues; then take each ready issue from
  plan to merged PR with a Sonnet builder and a fresh Opus reviewer, fixing only what blocks the main path
  and filing the rest as improvements. Use this whenever the user wants to plan or break down work into
  issues, "run the factory", work through open issues, pick up issue #N, go from idea to merged PRs, or
  says the label-by-label factory is too slow, even if they don't name the skill.
---

# Factory

This repo is built by an issue-driven agent factory (see `README.md` → "How this repo is built" and
`CLAUDE.md`). This skill runs it from a Claude Code session. You (Opus) are the orchestrator: a Sonnet
**builder** plans and codes, a fresh Opus **reviewer** checks the result, and you merge when the gates
pass. The maintainer is in the loop by exception: stop and ask whenever you're genuinely unsure.

Two principles shape everything below. Both came out of real runs:

1. **Urgency over completeness.** Before the project's first release (`Release phase: mvp` in
   `CLAUDE.md`), the goal is a main path that works end to end, then iteration. Only **blockers** are
   fixed inside a PR; everything else becomes an `improvement` issue. Treating every reviewer finding
   as a blocker cost ~4× the tokens for little benefit: adversarial reviewers always find one more
   edge case.
2. **Don't pay for context twice.** A cold subagent re-reads the issue, `CLAUDE.md`, the design and
   the code (40–90k tokens). So the agent that plans is the agent that builds, and it is the agent that
   fixes. Only reviewers are fresh, on purpose, and only once per issue.

The record stays complete: issues in the Factory-task format, `.factory/designs/issue-N.md` (the plan),
an implementation PR with "Checks run" and "Deviations", review comments and labels.

## Before you start

1. **Model.** You should be on Opus. If not, tell the user before orchestrating.
2. **Repo state.** `gh auth status` is OK, `main` is clean and up to date, and the `CLAUDE.md` checks pass
   on `main`. If `main` is red, stop.
3. **Read** `CLAUDE.md` (context, "Release phase", "Definition of checks pass", hard rules) and the stage
   prompts in `.claude/commands/`.
4. **Repo slug.** `gh repo view --json nameWithOwner -q .nameWithOwner`. Paste that exact value into
   every brief; never type it from memory.
5. **Run log.** Open or create `.factory/runs/<YYYY-MM-DD>.jsonl` (gitignored) and run
   `python .claude/skills/factory/scripts/runlog.py status` to see whether an earlier session left an
   issue mid-flight. If so, resume it at its recorded step instead of restarting. Log every step (below).
6. **Long runs.** Your own context grows with every step. After 2–3 issues, or when the session feels
   long, finish the current issue, write the run summary and suggest a fresh session. The run log lets
   it pick up cleanly.

## Mode 1: Brainstorm → issues

1. **Brainstorm with the user yourself** (Opus, main session): goals, users, constraints, out of scope,
   how they'll know it works. Read the code it touches. Identify the **main path**, the smallest chain of
   issues that makes the thing work end to end, and order it first.
2. **Propose a breakdown table:** slug, title, one-line goal, dependencies (main-path only; never make
   a main-path issue wait on a polish issue), areas, size (S/M; split anything L), and **risk tier**
   (see below). Mark polish and hardening items as `improvement`.
3. **Get one confirmation** ("create these N issues?"), because issues are visible to others.
4. **Draft bodies.** For several issues, spawn one Sonnet "Issue writer" per issue in parallel
   (`references/briefs.md`) with a brief file of the conclusions. For one or two small issues, write
   them yourself; a subagent costs more than it saves.
5. **Check and create, in dependency order** (`references/issue-format.md`), replacing `{{slug}}`
   placeholders with real `#N`. Apply the `improvement` label where it belongs.

## Mode 2: Run the factory

### Pick the work

`python .claude/skills/factory/scripts/queue.py` lists open issues by state and dependencies. In the MVP
phase, ready main-path issues come before ready `improvement` issues. Take ready issues in that order.
**At most two in flight** (see "Overlapping issues"); **merges are always one at a time.**

### Risk tiers

Decide the tier when you pick the issue (it may be in the issue already):

| Tier | When | Subagents |
|---|---|---|
| **Standard** | most S/M issues | builder (plans, then builds, then fixes) + 1 Opus code reviewer = **2** |
| **Critical** | privacy or security code; a new external dependency; a data contract other issues build on; anything you'd hate to get wrong | + 1 Opus **design reviewer** between plan and build = **3** |

### For each issue N

Create a worktree on a **`local/issue-N`** branch: `git worktree add ../<repo>-issue-N -b local/issue-N
origin/main`. The `local/` prefix keeps the GitHub Actions factory (which reviews `factory/issue-*`
branches) from also reviewing skill PRs. Pass subagents
**absolute paths**. Set the label `factory:designing`. Log each step with `runlog.py add`.

**1. Plan (Sonnet builder, phase 1).** Spawn the builder with the "Builder" brief, phase 1: it writes the
plan to `.factory/designs/issue-N.md`, commits it on `local/issue-N`, pushes, and **stops**, returning
the plan. Keep the agent's id: the same agent builds and fixes.

**2. Plan check.**
- **Standard tier:** you skim the plan yourself, a couple of minutes. Does it meet the acceptance
  criteria? Does it fit the code? Does it add scope the issue didn't ask for? Does it make an
  unverified assumption about external data or formats (see "Reality check")? If something's off,
  send the corrections to the builder (`SendMessage`) and have it update the plan. No separate review.
- **Critical tier:** spawn a fresh Opus "Design reviewer". `REVISE` → send the changes to the builder;
  you check they landed (no second review). `ASK` → stop and ask the maintainer. Reviewer `notes` go to
  the builder as implementation guidance.
- Record the approval as a comment on the issue: it replaces the human `design:approved` label. Never
  add that label: it would start the GitHub Actions build. Set `factory:building`.

**3. Build (same builder, phase 2).** `SendMessage` the builder: "plan approved, build it" (plus any notes).
It implements, runs every `CLAUDE.md` check until green, opens "feat: … (#N)" (with the plan file in the
diff, "Checks run" and "Deviations") and sets `factory:review`. Verify the PR exists.

**4. Review ∥ CI.** Start the fresh Opus "Code reviewer" **while CI runs**; they're independent. The
reviewer tags every finding **`blocker`** or **`improvement`** (definitions in the brief). Wait on CI with
a **bounded** poll (`references/gates.md` → gate 1), never an unbounded `--watch`.

**5. Fix blockers only (same builder).** If there are blockers, `SendMessage` the builder the blocker list,
with reproductions. It fixes them, adds a test that fails without each fix, re-runs the checks and
pushes. **No re-review:** you verify each blocker yourself with its reproduction plus green CI. Allow
two fix rounds; if blockers persist, stop and ask.

**6. Improvements.** Write **one** `improvement` issue yourself listing the reviewer's non-blocking
findings (with their reproductions), `Depends on #N`, labelled `improvement`. Don't add it as a
dependency of any main-path issue.

**7. Merge.** Check every gate in `references/gates.md` yourself on the PR's current commit, then
`gh pr merge <PR> --merge --admin --delete-branch`. Post the one-line merge note, update the run log, pull
`main` and remove the worktree. Run `queue.py` again.

### Overlapping issues

While issue A builds or waits for CI, you may start the **plan** of an independent ready issue B (no
dependency either way, and no files A is changing). It uses its own worktree and builder. Keep at most two
issues in flight and merge one at a time. After A merges, update B's branch with `main`
(`gh api -X PUT repos/<owner/repo>/pulls/<PR>/update-branch`) before B's review, so B is reviewed against
current code.

### Reality check

Agents may not read real user data (`CLAUDE.md`), so they can only *assume* how external formats look. When
a plan or review rests on an assumption about real data that the code, the issue or a cited spec can't
verify (for example, a file layout or a field that "probably" exists), **don't build on it.** Stop and ask
the maintainer to confirm it, ideally with a script they run locally that prints only the **shape** of
their data (record types, field names, counts), never values. Record the answer on the issue.

## Stopping to ask

Stop and ask when you're genuinely unsure, and in these cases:
- a design reviewer says `ASK`, or a plan's open questions need product or priority judgment;
- a reviewer's confidence is below high on a **blocker**;
- blockers remain after two fix rounds, or checks can't be made green;
- a merge gate fails (workflows, dependencies the plan didn't name, version, `CLAUDE.md`);
- two consecutive rounds find blockers in the same area: the premise may be wrong, so ask whether to
  keep, simplify or split that part out;
- an unverifiable assumption about real data (see "Reality check");
- a subagent added `factory:blocked`.

**How to stop:** add `factory:blocked`, post a comment with the question, the options and your
recommendation, and ask in the session with `AskUserQuestion`. Record the answer on the issue, remove the
label and continue.

## Coexisting with the GitHub Actions factory

The two modes never trigger each other: skill branches are `local/issue-N` (the Actions review only
matches `factory/issue-*`), and the skill never adds `factory:design` or `design:approved` or writes
`@claude` in a comment, which are what start the Actions workflows. Status labels (`factory:designing`, `factory:building`, `factory:review`,
`factory:blocked`) start nothing.

## When you're done

`python .claude/skills/factory/scripts/runlog.py summary` prints per-issue wall time, rounds and subagent
tokens by model. Report: issues merged (PR links), improvements filed, blocked issues and why, decisions
made on the maintainer's behalf, and the cost summary. If a problem was a factory problem rather than a
project problem, say so: it belongs in the factory template as a lesson.

## Reference files

- `references/issue-format.md`: the Factory-task issue body and its quality bar.
- `references/briefs.md`: prompts for every subagent and the builder follow-up messages.
- `references/gates.md`: merge gates, bounded CI polling, and the merge command.
- `scripts/queue.py`: the ready-issue queue (dependency order; improvements last in the MVP phase).
- `scripts/runlog.py`: the run log (`add`, `status`, `summary`).
