# Design: convert accepts a claude.ai export (#47)

## Problem
`pensieve-traces convert` reads only Claude Code input, so a claude.ai export (read by `read_export`, #46) can't be turned into STS or ConvoMem traces from the command line. Users must call the Python API and then the writers by hand, skipping the CLI's redaction and output guards.

## Proposed approach
- NEW public `load_sessions(path: Path, source: str | None = None) -> list[Session]` in `pensieve_traces/cli.py`, replacing `_load` (same body, now the one input-detection function; #55 calls it per input, any mix, and concatenates). `source` is `None` (auto), `"claude-code"` or `"claude-ai"`.
- Auto-detection: a file named `conversations.json` is claude.ai; any other file is a Claude Code session. A directory holding `conversations.json` and no `*.jsonl` is claude.ai (`read_export`); one holding `*.jsonl` and no `conversations.json` is Claude Code (`read_project`).
- Errors (`CliError`, one line, exit 1, nothing written): both present: `ambiguous input {path}: it holds conversations.json and *.jsonl files; pass --from claude-code or --from claude-ai`; neither: `no *.jsonl session files or conversations.json in {path}`; `--from claude-ai` on a directory without the file: `no conversations.json in {path}`. Missing path keeps today's message.
- `--from {claude-code,claude-ai}` on `convert` (default: auto) forces the reader.
- `ClaudeAiParseError` joins the caught exceptions in `main`.
- Output guard unchanged in logic: `_check_out_dir` already refuses `--out` equal to or inside the input directory, or the parent directory of an input file. It runs before any write for both an export directory and a `conversations.json` path (the file case refuses its parent, the export directory). Tests lock this in.
- Writers, redaction defaults and `--no-redact` are untouched. `--include-sidechains` is a no-op for claude.ai (no sidechain messages).

## Alternatives rejected
- Only `--from`, no auto-detect: every call needs a flag the layouts already imply.
- Sniffing JSON contents: slower, can misfire; file name and layout are what the issue specifies.

## Files & interfaces touched
- CHANGED `pensieve_traces/cli.py`: `load_sessions`, `--from`, error handling.
- CHANGED `tests/test_cli.py`.
- CHANGED docs: `README.md` ("Reading claude.ai exports" drops the "does not accept exports" sentence; "Converting to STS" and "Converting to ConvoMem conversations" cover exports, `--from` and the guard), `CHANGELOG.md`, `convert` help text for `input`. `CLAUDE.md` is not touched.

## Test plan
Offline, synthetic `tests/fixtures/claude_ai/export/` only, with the fake teich fixture:
- STS from the export directory and from `conversations.json`: one `<uuid>.jsonl` per conversation, header `harness: "claude.ai"` and `pensieveRedacted`.
- `convomem-conversations` writes both files from the export.
- `--out` equal to the export directory, inside it, and beside `conversations.json`: refused, directory unchanged.
- Ambiguous directory, empty directory, `--from` override each way, `--from claude-ai` without the file.
- Missing `redact` extra: error, nothing written; `--no-redact` works with a warning.
- A mixed-call test of `load_sessions` (Claude Code dir and export) for #55.
- Malformed `conversations.json`: one-line error.

## Risks
- Privacy: the reader keeps injected prompts, thinking and tool output; redaction stays on by default. No real exports used. The manifest and memories are never opened.
- Data loss: a user passing `--out` as the export directory; guarded and tested.
- Spec: whether the Hub renders `harness: "claude.ai"` is unverified (maintainer check, not blocking). No new dependencies.

## Open questions
- None. (Zip input is #58; conversation subsets are out of scope.)

## Size estimate
S

## Implementation notes

- Built as planned, PR #60: public `load_sessions(input_path, source=None)` in `pensieve_traces/cli.py` replaces `_load` (no printing; #55 calls it per input) and `convert --from {claude-code,claude-ai}` overrides auto-detection. `ClaudeAiParseError` is caught in `main`. `_check_out_dir` is unchanged and already covers the export directory and the directory of `conversations.json`; tests lock that in.
- Code review (6055447571): READY, no blockers. Verified by the reviewer: redaction over every export field with the real teich extra, the data-loss guard (input checksum unchanged), and byte-identical Claude Code output against `main`.
- Follow-ups → #62: exact-id ConvoMem test, `load_sessions` accepting `str`, one `--no-redact` warning instead of one per session, guard wording for exports, header-only STS files for empty conversations.
- Open for the maintainer: whether the Hub renders `harness: "claude.ai"` (upload one synthetic trace to a private dataset).
