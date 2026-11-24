# Factory-task issue format

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
- Verified against real data? Say which assumptions about external formats or data were confirmed
  (and how) and which are assumptions. Unverified ones are for the maintainer's reality check.
- Risk tier: standard | critical (privacy/security code, a new external dependency, a data contract
  other issues build on).

### Acceptance criteria

- [ ] Checkable statements: names of functions/classes/CLI flags, behaviors, error handling.
- [ ] What the tests prove, using offline fixtures only.
- [ ] Docs updated (README section, CHANGELOG).

### Out of scope

What this issue deliberately doesn't do, and which issue does it instead (`#N`) if any.

### Areas affected

One or more of the options in the issue template's "Areas affected" dropdown.
```

Issues that are polish or hardening rather than the main path get the `improvement` label. A
main-path issue never depends on an `improvement` issue.

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
- **Hard rules are reflected.** If `CLAUDE.md` says redaction is on by default or data is never
  committed, the issue's criteria say how that applies here.
- **Nothing is invented.** Where the brainstorm left something open, the issue says it's open and makes
  it part of the design, or leaves it for the maintainer. Don't guess at it.

## Dependency placeholders

When drafting several issues at once, refer to the others as `{{slug}}` (e.g.
`Depends on {{canonical-model}} (canonical model)`). The orchestrator creates the issues in dependency
order and replaces each placeholder with the real `#N`. `scripts/queue.py` reads dependencies from the
"Depends on #A, #B" line, so keep them on that one line.
