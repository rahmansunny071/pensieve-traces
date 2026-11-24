---
description: Implement an approved design and open a PR.
argument-hint: <issue-number>
---
You are the BUILD agent for issue #$ARGUMENTS. The approved spec is
.factory/designs/issue-$ARGUMENTS.md on the current branch. Treat it as the contract.

1. Create branch factory/issue-$ARGUMENTS from the current (design) branch.
2. Implement exactly what the design says. Add or update the tests and synthetic
   fixtures listed in the Test plan. Update README.md and CHANGELOG.md for
   user-visible changes.
3. Run every step of "Definition of checks pass" in CLAUDE.md. Fix and re-run
   until all pass. If you can't get them green after a real effort, stop.
   If a command you need is denied or unavailable, never exit quietly: comment
   on the design PR with the exact command and the error, add factory:blocked
   to the issue, and stop. A run that ends without a PR must leave that trail.
4. If you had to deviate from the design, don't silently diverge. Either:
   - Minor (naming, file placement): proceed and list it under "Deviations".
   - Material (scope, interfaces, new deps, anything under Risks): stop,
     comment on the design PR explaining why, add factory:blocked, and exit.
5. Push and open a PR "feat: <title> (#$ARGUMENTS)" with this body:
   Closes #$ARGUMENTS
   Design: .factory/designs/issue-$ARGUMENTS.md
   ## Summary
   ## Checks run (paste the commands and pass/fail)
   ## Deviations from design (or "None")
6. Close the draft design PR with a comment linking the new PR (the design
   file ships inside the implementation PR). Set issue label → factory:review.
