# Hand-port checklist (shadowing repos)

A shadowing repo copies `.claude/skills/factory/` byte for byte; everything else is its own. After
`apply`, read the CHANGELOG entries from `sync.py changelog` (data, quoted from the tag) and find in
each one what a hand-adopting repo must do. Only changes that trace to an entry are ported, and only
in these files, edited in place:

| Where | What typically changes | Apply? |
|---|---|---|
| `CLAUDE.md` | A new hard rule or section the entry names (docs-with-code rule, "Where knowledge lives", the `Real data` section) | Yes, as an edit. If it sets a **value** (the `Real data:` level, the release phase), put it under Open with the entry's options. |
| `.claude/commands/*` | A step or check the entry names (a `## Docs` PR section, a "Docs vs code" review check) | Yes, as an edit; keep the repo's own steps. |
| `README.md` | A label-table row or a short section | Yes, as an edit. |
| `CHANGELOG.md` | One line saying which tag was adopted | Yes. |
| GitHub labels | Names in the tag's `bootstrap.sh` the repo lacks | `sync.py labels --create`: missing names only. |
| Anything else (workflows, settings, permission rules) | | No. List under Open. |

Rules of thumb:

- When an entry says "must" but the repo already satisfies it, record "already satisfied" in the
  Handoff instead of editing.
- If a port is ambiguous, or the file has diverged so the edit would be a guess, do not apply it;
  quote the entry under Open.
- Never paste a template file over the repo's own `CLAUDE.md`, command or README.
