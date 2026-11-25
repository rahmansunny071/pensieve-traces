---
title: "How this repo is built"
description: "The agent factory: issue-driven designs, builds and reviews."
---

# How this repo is built: an agent factory

Work happens through an issue-driven factory, either on Claude Code GitHub Actions (below) or from a Claude Code session with the `factory` skill. Agents write short designs, review them, build, test and open PRs. In the Actions factory only the maintainer starts or advances work and merges to `main`; the `factory` skill merges on green gates only if the maintainer's permissions allow its `gh pr merge … --admin` command (see the permission rule below). No agent publishes a release or uploads anything.

1. Open an issue with the **Factory task** template.
2. Add `factory:design`. The design agent writes `.factory/designs/issue-N.md` and opens a draft design PR.
3. Review it; iterate with `@claude …` comments or edit it yourself.
4. Add `design:approved` to the design PR. The build agent implements it on `factory/issue-N`, runs the checks until green and opens a PR.
5. The review agent checks the PR against the design and `CLAUDE.md`.
6. Merge. Releases stay manual.

| Label | Applied by | Meaning |
|---|---|---|
| `factory:design` | maintainer, on an issue | Ask an agent for a design |
| `factory:designing` | agent | Design in progress / awaiting review |
| `design:approved` | maintainer, on the design PR | Signed off: start building |
| `factory:building` | agent | Implementation in progress |
| `factory:review` | agent | Implementation PR open, awaiting merge |
| `factory:blocked` | agent | Agent hit something it shouldn't decide |
| `factory:ready-to-merge` | factory skill | Every gate passed but the merge was refused: you merge it |

Agent rules live in [`CLAUDE.md`](https://github.com/rahmansunny071/pensieve-traces/blob/main/CLAUDE.md); the stage prompts in [`.claude/commands/`](https://github.com/rahmansunny071/pensieve-traces/tree/main/.claude/commands).

The **`factory` skill** (`.claude/skills/factory/`) runs the same pipeline from a Claude Code session. Two of its rules:

- **Docs ship with the code:** every PR updates the docs its change affects, and its Handoff says which (or why none). Older mismatches become sub-issues of the "Documentation tech-debt" issue (#43).
- **Ready to merge:** if every gate passes but the merge is refused (the harness denies it or GitHub rejects it), the skill never works around it. It labels the PR `factory:ready-to-merge`, comments the exact merge command and carries on; `queue.py` and the end-of-run report list what waits for you. To let it merge by itself instead, see [Letting the factory merge for you](#letting-the-factory-merge-for-you).

Two companion skills ship with it. **`sync-root-factory`** (`.claude/skills/sync-root-factory/`) brings the factory skill up to the latest agent-factory release as a PR ("sync the factory to the latest release"); the version this repo is on is in `.claude/skills/factory/VERSION`. **`factory-lesson`** (`.claude/skills/factory-lesson/`) files a factory problem as a `lesson` issue in agent-factory ("file a factory lesson: …"), after one confirmation and with no real data in the text.

## Letting the factory merge for you

Add this [permission rule](https://code.claude.com/docs/en/permissions) yourself, to `.claude/settings.local.json` (keep it out of git) or `~/.claude/settings.json`, not to the committed `.claude/settings.json`. It lets the `factory` skill run its merge command with your ruleset bypass (`--admin`), which it needs because a PR's author can't approve their own PR. Because it hands an agent your bypass, the repo doesn't ship it:

```json
{ "permissions": { "allow": ["Bash(gh pr merge * --merge --admin --delete-branch)"] } }
```

The rule matches only commands that start with `gh pr merge` and end in exactly `--merge --admin --delete-branch`, which is how the skill runs the merge (a trailing `-R` isn't covered). The skill still merges only when every gate passes, and never works around a refused merge.
