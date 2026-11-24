# Changelog

- Unreleased: Repository scaffold and agent factory (CLAUDE.md, factory commands, workflows, CI).
- Unreleased: Canonical session model (`Session`, `Message`, `ToolCall`, `Usage`, `Role`) with lossless JSON round-trip (#1).
- Unreleased: Claude Code reader (`read_session`, `read_project`) from raw session JSONL to the canonical `Session` (#2).
- Unreleased: Redaction pass on by default (`redact_session`, `prepare_for_output`, optional `pensieve-traces[redact]` extra using teich) (#3).
- Unreleased: STS writer (`write_session`, `session_to_sts_lines`) and `pensieve-traces convert --to sts` CLI (#4).
