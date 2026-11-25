# Design: Docs site at rahmansunny071.github.io/pensieve-traces (#81)

## Problem
The README (241 lines) holds all documentation. Readers need a navigable site, in the style of convomem-arena's, and the README should become a landing page with a quick start. Nothing may be lost in the move.

## Proposed approach
**Needs the maintainer's explicit approval (each exactly):**
- (a) NEW `.github/workflows/docs.yml`: triggers `push` to `main` with paths `docs/**`, `mkdocs.yml`, `overrides/**`, `CHANGELOG.md`, `.github/workflows/docs.yml`, plus `workflow_dispatch`; `permissions: contents: write`; concurrency group `docs`; steps: checkout (`fetch-depth: 0`), setup-python 3.12, `pip install "mkdocs-material>=9.5"`, git bot identity, `mkdocs gh-deploy --strict --force`. No secrets, no other service.
- (b) NEW extra in `pyproject.toml`: `docs = ["mkdocs-material>=9.5"]`. Base install unchanged; `uv.lock` updated only by `uv lock`.
- (c) Repo setting: GitHub Pages served from the `gh-pages` branch (the orchestrator enables it after the first deploy).

**Nav** (README section it comes from):
- Overview `index.md`: intro, outputs table, diagram, Privacy note.
- Get started: Install (Redaction install line, Development setup); Convert Claude Code sessions (Converting to STS); Convert a claude.ai export (Reading claude.ai exports, STS export bullets); Build a ConvoMem dataset (Building a ConvoMem dataset).
- Concepts: Canonical model; Readers (Claude Code, claude.ai); Redaction and privacy (Redaction, with the coverage table); Output formats (STS; ConvoMem conversations; ConvoMem dataset layout and manifest).
- Guides: Uploading to the Hub; Checking a ConvoMem dataset; Topics.
- Reference: CLI (convert, build convomem, check-convomem, upload, flags copied from `--help`); Python API (the "From Python" notes); Changelog; How this repo is built (factory, labels table, skills, "Letting the factory merge for you"); Development; Limitations (the "Not covered" notes, unverified `claude.ai` harness rendering).

**README that remains:** title, tagline, "Docs:" link, outputs table, diagram, Privacy note, Quick start (install `[redact]`, one `convert`, one `build convomem`), stub "How this repo is built" (one line linking to the site) containing the stub "Letting the factory merge for you" (title exact, links to the site page), Author. No stale copies remain; CHANGELOG entry added.

**Changelog:** `docs/reference/changelog.md` front matter plus `--8<-- "CHANGELOG.md"` via `pymdownx.snippets` (`base_path: ["."]`, `check_paths: true`). The file is rendered, not copied; the workflow's path filter includes it.

**Branding:** `material/memory` logo icon, custom accent (teal), own `favicon.svg`, `overrides/main.html`, `stylesheets/extra.css`, `javascripts/lede.js` (CSS class prefix `pt-`), `strict: true`, same theme features as convomem-arena.

**Local strict check:** `uv sync --locked --extra dev --extra docs && uv run --no-sync mkdocs build --strict`, plus `mkdocs serve` to look at it.

## Alternatives rejected
- Copying CHANGELOG.md into docs: goes stale.
- Dropping the README stub: the `factory` skill links to "Letting the factory merge for you" (and, in SKILL.md line 16, to "How this repo is built"); the skill is template-synced, not edited here.

## Files & interfaces touched
- NEW `mkdocs.yml`, `docs/**` (about 17 pages, `assets/favicon.svg`, `stylesheets/extra.css`, `javascripts/lede.js`), `overrides/main.html`
- NEW `.github/workflows/docs.yml` (a)
- CHANGED `pyproject.toml`, `uv.lock` (b)
- CHANGED `README.md`, `CHANGELOG.md`
- NEW `.factory/designs/issue-81.md`
- Not touched: `ci.yml`, CLAUDE.md, `.claude/skills`, code.

## Test plan
No Python tests. Run `mkdocs build --strict` (no warnings); the four CI checks; diff README on `main` against the site section by section; compare CLI reference to each `--help`; grep for links into removed README anchors. Live check of the site once after merge.

## Risks
- Privacy: docs use placeholders only (`path/to/<project-dir>`, `path/to/claude-export`), no real traces. Redaction defaults documented unchanged.
- Spec conformance: no output change.
- New dependency: `mkdocs-material` only in the optional `docs` extra.
- The docs workflow gets `contents: write` and pushes `gh-pages`; per merge gate 6 the maintainer merges it.
- Site is public: it republishes README text already public.

## Open questions
Answered by the maintainer (recorded on PR #82): (a), (b) and (c) approved exactly as named above. `ci.yml` is NOT changed here; a strict build in CI becomes a separate issue. Both README stubs are kept ("How this repo is built" and "Letting the factory merge for you"), each linking to its site page. Still unverified until the build: mkdocs-material resolution under the lock.

## Size estimate
M
