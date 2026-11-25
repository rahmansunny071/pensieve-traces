# Factory-task issue format

Work is organised in **lanes** (parent issues) holding **ordered issues** (native sub-issues). Both use
the formats below. Priority is exactly one label: `P0` (must have now), `P1` (next), `P2` (later),
`P3` (someday or hardening). A missing label counts as `P2`.

## Lane issue (label `lane` + a `P` label)

```markdown
### Goal
The feature this lane delivers, in user-visible terms, and how we'll know it's done.

### Issues, in order
1. #A: <title> (one line)
2. #B: <title>
3. <stub: title only, detailed when it gets close>

### Depends on
- Lane #X (why), or issue #Y (why), or "none". These are also native "blocked by" links.

### Out of scope
What this lane deliberately leaves to other lanes.
```

## Issue (a sub-issue of a lane)

Every issue uses the same sections as `.github/ISSUE_TEMPLATE/factory-task.yml`, so issues from this
skill and issues opened by hand look the same. Write the body in Markdown with these headings, in this
order:

```markdown
### Goal

What should be true when this is done, in terms of user-visible behavior. One short paragraph, plus a
usage example (a command or code snippet) when there is one.

### Context & pointers

- Depends on #A (short name) and #B (short name).        ← omit the line if there are none
- The modules, files and docs it touches, with paths.
- Specs or external references, with exact URLs. If something is unverified, say so explicitly
  ("schema not verified — the design must cite the official docs or block").
- Reuse opportunities and known pitfalls from the brainstorm.
- Lane and position: "Lane #L, step 2 of 3" (also visible from the native parent link).
- Verified against real data? Say which assumptions about external formats or data were confirmed
  (and how) and which are assumptions. Unverified ones are for the maintainer's reality check.
- Risk tier: standard | critical (privacy/security code, a new external dependency, a data contract
  other issues build on).

### Acceptance criteria

- [ ] Checkable statements: names of functions/classes/CLI flags, behaviors, error handling.
- [ ] What the tests prove, using offline fixtures only.
- [ ] Docs updated, named exactly: e.g. `README.md` "Usage" section, `CHANGELOG.md`, the docstring of
      `pkg.writer.write_sts`. Every doc the change makes wrong or stale counts, not just new ones.

### Out of scope

What this issue deliberately doesn't do, and which issue does it instead (`#N`) if any.

### Areas affected

One or more of the options in the issue template's "Areas affected" dropdown.
```

Issues that are polish or hardening rather than the main path get the `improvement` label. A
main-path issue never depends on an `improvement` issue.

## Documentation todo (a sub-issue of "Documentation tech-debt")

A doc that's wrong or stale and isn't fixed by the PR at hand (a reviewer's `doc-debt` finding, or one
noticed while planning). File it with `scripts/lanes.py doc-debt add --title … --body-file …`, which
labels it `documentation`, `improvement`, `P2` and links it under the open issue titled exactly
"Documentation tech-debt" (created if missing). The body says:

```markdown
`<path>` (<section>) says <what it says>, but <what the code does, with a file:line or command>.

**Fix:** <the exact change>.

Found in <review thread / PR / planning link>. Can ride along with the next PR that touches `<path>`.
```

## Quality bar

An issue is ready to create when a designer who has read only this issue, `CLAUDE.md` and the code
could write a design without asking anything. In practice:

- **The goal is behavior, not implementation.** "Read a Claude Code session file into the canonical
  model" rather than "write a parser class".
- **Every acceptance criterion is checkable**, and most are testable offline. Avoid "works well",
  "is robust" or "handles edge cases" unless they're spelled out.
- **Dependencies are real**: B depends on A only if B needs A's code or decisions. False dependencies
  serialize work for nothing.
- **Scope is one PR.** If the criteria list runs past roughly 8 items, or spans several subsystems, split
  it.
- **Docs are named.** The "Docs updated" criterion lists the specific files and sections, so the
  reviewer can check them.
- **Hard rules are reflected.** If `CLAUDE.md` says redaction is on by default or data is never
  committed, the issue's criteria say how that applies here.
- **Nothing is invented.** Where the brainstorm left something open, the issue says it's open and makes
  it part of the design, or leaves it for the maintainer. Don't guess at it.

## Rolling-wave detail

Only the next one or two issues per lane need the full format and quality bar below. Later issues in a lane
can be **stubs**: a title, one-line goal and dependencies. Detail them (with an Issue writer) when they
become the lane's next issue but one. Plans written too early go stale.

## Dependency placeholders

When drafting several issues at once, refer to the others as `{{slug}}` (e.g.
`Depends on {{canonical-model}} (canonical model)`). The orchestrator creates the issues in dependency
order and replaces each placeholder with the real `#N`. `scripts/queue.py` reads dependencies from the
"Depends on #A, #B" line, so keep them on that one line.
