# Changelog

- Unreleased: Repository scaffold and agent factory (CLAUDE.md, factory commands, workflows, CI).
- Unreleased: Canonical session model (`Session`, `Message`, `ToolCall`, `Usage`, `Role`) with lossless JSON round-trip (#1).
- Unreleased: Claude Code reader (`read_session`, `read_project`) from raw session JSONL to the canonical `Session` (#2).
- Unreleased: Redaction pass on by default (`redact_session`, `prepare_for_output`, optional `pensieve-traces[redact]` extra using teich) (#3).
- Unreleased: STS writer (`write_session`, `session_to_sts_lines`) and `pensieve-traces convert --to sts` CLI (#4).
- Unreleased: `pensieve-traces upload` to a private Hugging Face dataset (optional `pensieve-traces[hub]` extra); STS writer adds the `pensieveRedacted` header key (#6).
- Unreleased: `convert` refuses an `--out` directory that would overwrite the input session files (#36).
- Unreleased: ConvoMem conversations export (`convert --to convomem-conversations`, `write_conversations`, `sessions_to_conversations`) with a message-ID map (#5).
- Unreleased: README states that uploading is always intentional: only `pensieve-traces upload` contacts the Hub.
- Unreleased: Factory skill updated to agent-factory v0.6.0 (docs-with-code rule and Documentation tech-debt tracker; ready-to-merge hand-off when a merge is refused, agent-factory#32); new `factory:ready-to-merge` label.
- Unreleased: Factory skill synced to agent-factory v0.8.0 with its new `sync-root-factory` skill (version marker `.claude/skills/factory/VERSION`), plus the `factory-lesson` skill; the Reality check now reads a `Real data:` line from `CLAUDE.md` (missing means `never`).
- Unreleased: claude.ai export reader (`pensieve_traces.readers.claude_ai.read_export`, `ClaudeAiParseError`) from `conversations.json` to canonical `Session`s; README intro, readers diagram and STS status row updated (#46, #44).
- Unreleased: ConvoMem builder 1/3: `OpenAICompatibleClient` (stdlib, local Ollama by default) and `extract_candidates` find candidate memory facts in conversations, with a per-window cache; prompts adapted from Salesforce ConvoMem (Apache-2.0, `NOTICE`) (#8).
- Unreleased: `convert` accepts a claude.ai export (directory or `conversations.json`) for `sts` and `convomem-conversations`; input is auto-detected, `--from {claude-code,claude-ai}` overrides, `load_sessions` is the reusable loader; `--out` inside an export directory is refused (#47).
- Unreleased: ConvoMem builder 2/3: `build_items` turns candidate facts into evidence items (question, answer, verbatim evidence) for the six ConvoMem categories, with code checks (one conversation per evidence message, verbatim evidence, chronology, duplicate questions, convomem-arena's filler check) and model verification with ConvoMem's judge prompts; every model call is cached; shared reply parsing and cache moved to `builders/convomem/_common.py` (#54).
