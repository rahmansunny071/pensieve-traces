# Changelog

## Unreleased

## 0.1.0 (2026-11-25)

- Installs come from GitHub, pinned to the release tag; the package is not on PyPI; `__version__` reads the installed version; install hints point at GitHub (#86).
- Repository scaffold and agent factory (CLAUDE.md, factory commands, workflows, CI).
- Canonical session model (`Session`, `Message`, `ToolCall`, `Usage`, `Role`) with lossless JSON round-trip (#1).
- Claude Code reader (`read_session`, `read_project`) from raw session JSONL to the canonical `Session` (#2).
- Redaction pass on by default (`redact_session`, `prepare_for_output`, optional `pensieve-traces[redact]` extra using teich) (#3).
- STS writer (`write_session`, `session_to_sts_lines`) and `pensieve-traces convert --to sts` CLI (#4).
- `pensieve-traces upload` to a private Hugging Face dataset (optional `pensieve-traces[hub]` extra); STS writer adds the `pensieveRedacted` header key (#6).
- `convert` refuses an `--out` directory that would overwrite the input session files (#36).
- ConvoMem conversations export (`convert --to convomem-conversations`, `write_conversations`, `sessions_to_conversations`) with a message-ID map (#5).
- README states that uploading is always intentional: only `pensieve-traces upload` contacts the Hub.
- Factory skill updated to agent-factory v0.6.0 (docs-with-code rule and Documentation tech-debt tracker; ready-to-merge hand-off when a merge is refused, agent-factory#32); new `factory:ready-to-merge` label.
- Factory skill synced to agent-factory v0.8.0 with its new `sync-root-factory` skill (version marker `.claude/skills/factory/VERSION`), plus the `factory-lesson` skill; the Reality check now reads a `Real data:` line from `CLAUDE.md` (missing means `never`).
- claude.ai export reader (`pensieve_traces.readers.claude_ai.read_export`, `ClaudeAiParseError`) from `conversations.json` to canonical `Session`s; README intro, readers diagram and STS status row updated (#46, #44).
- ConvoMem builder 1/3: `OpenAICompatibleClient` (stdlib, local Ollama by default) and `extract_candidates` find candidate memory facts in conversations, with a per-window cache; prompts adapted from Salesforce ConvoMem (Apache-2.0, `NOTICE`) (#8).
- `convert` accepts a claude.ai export (directory or `conversations.json`) for `sts` and `convomem-conversations`; input is auto-detected, `--from {claude-code,claude-ai}` overrides, `load_sessions` is the reusable loader; `--out` inside an export directory is refused (#47).
- ConvoMem builder 2/3: `build_items` turns candidate facts into evidence items (question, answer, verbatim evidence) for the six ConvoMem categories, with code checks (one conversation per evidence message, verbatim evidence, chronology, duplicate questions, convomem-arena's filler check) and model verification with ConvoMem's judge prompts; every model call is cached; shared reply parsing and cache moved to `builders/convomem/_common.py` (#54).
- Redaction coverage on chat personal data is measured and pinned (`tests/test_redact_chat_coverage.py`, README "Redaction" table, teich 0.3.8); `upload` warns before the confirmation prompt, also with `--yes`, how many files are claude.ai chats and what redaction does not catch; new `upload.count_harness` (#48).
- ConvoMem builder 3/3: `pensieve-traces build convomem INPUT... --out DIR --model M` builds a ConvoMem dataset (evidence items for six categories, filler without evidence conversations, `build_manifest.json`) from Claude Code sessions and claude.ai exports in any mix; one redaction pass before any model call, local-only endpoint unless `--allow-remote-model`, optional `--topics`, cache beside the dataset, no-clobber output (`builders/convomem/dataset.py`, `topics.py`) (#55).
- `pensieve-traces check-convomem DIR` and `builders.convomem.check_dataset(path) -> list[Problem]` check the structure of a generated ConvoMem dataset (category and count folders, verbatim evidence from distinct conversations, changing-evidence order, filler without evidence conversations); README documents running convomem-arena's full check once convomem-arena#18 lands (#9).
- Redaction replaces an email address directly followed by a period (teich 0.3.8 left `mail a@b.invalid.` unredacted) with the same placeholder as the bare address, in text, metadata keys and values and tool arguments (`_EmailPeriodGuard` in `redact.py`); README "Redaction" table and pinned coverage rows updated (#67).
- README: the ConvoMem dataset row says local-folder runs need convomem-arena#18, and the factory intro matches opt-in merges by the `factory` skill (#74).
- README: ConvoMem conversations no longer claim convomem-arena loads them from a local folder today (needs convomem-arena#18) (#77).
- README: new "Letting the factory merge for you" subsection (the heading the `factory` skill points to) holds the opt-in merge permission rule (#79).
- Documentation site at https://rahmansunny071.github.io/pensieve-traces/ (MkDocs Material: `mkdocs.yml`, `docs/`, `overrides/`), published to `gh-pages` by the new `.github/workflows/docs.yml`; new optional `docs` extra (`mkdocs-material`); the README is now a landing page with a quick start, and the sections that moved are on the site (#81).
- package metadata links the docs site (`Documentation`, `Changelog` in `[project.urls]`); the repo's About box links it too (#84).
