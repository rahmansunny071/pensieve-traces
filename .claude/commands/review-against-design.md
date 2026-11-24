---
description: Review an implementation PR against its approved design.
argument-hint: <pr-number>
---
You are the REVIEW agent for PR #$ARGUMENTS. Don't push commits.

1. `gh pr view $ARGUMENTS` and `gh pr diff $ARGUMENTS`. Find the design file
   referenced in the PR body and read it.
   Verify the PR body's "Checks run" against CI with `gh pr checks $ARGUMENTS`
   (CI runs the same checks as CLAUDE.md). Don't try to run tests or linters
   yourself; this job is read-only. If CI is still pending, say so.
2. Post ONE review comment with:
   - **Design conformance:** each "Proposed approach" and "Test plan" item → ✅/⚠️/❌
   - **Undisclosed deviations:** anything not listed under "Deviations"
   - **CLAUDE.md hard rules:** any violation (real traces or personal data in
     fixtures, redaction not on by default, uploads or credentials, paid model
     calls, workflows touched, unapproved or non-optional heavy dependencies,
     version bump)
   - **Spec conformance:** for writers, does the output match the cited spec?
   - **Bugs / risky code:** specific, with file:line. Irreversible loss or overwrite of the user's existing data (source files, history, remote repos) on a plausible invocation, even a mistaken one, when a cheap guard would prevent it is must-fix.
   - **Verdict:** Ready for maintainer / Needs changes
3. Keep it short. No style nits that ruff already enforces.
