# Design: Claude Code reader: raw session JSONL to canonical Session (#2)

## Problem
Users have Claude Code session files (`<session>.jsonl`) full of internal bookkeeping (file backups, API request blobs, cost state) mixed with the real conversation. There is no way yet to load one into the canonical `Session` model. The reader must return only the conversation, so nothing internal can leak into later output.

## Proposed approach
New `pensieve_traces/readers/claude_code.py`, stdlib only:
- `read_session(path) -> Session` streams the file line by line. Bad JSON raises `ClaudeCodeParseError(path, line_no)`; a non-object line is treated the same way.
- `read_project(dir) -> list[Session]` reads each `*.jsonl` directly in the directory (non-recursive), sorted by `started_at`.
- Allowlist, not blocklist: only `user`, `assistant`, `system` records become messages. Everything else is skipped with `logger.debug`, so unknown future types are dropped by default. `ai-title` sets `Session.title` (last wins).
- Assistant blocks: `text` joined into `text`, `thinking` into `reasoning`, `tool_use` into `ToolCall(id, name, json.dumps(input))`. Unknown block types are skipped.
- User blocks: `text` becomes a `user` message. Each `tool_result` becomes a separate `tool` message with `tool_call_id`, `is_error` from `is_error`, and list-form content flattened to text.
- Mapping: `uuid`->`id`, `parentUuid`->`parent_id`, `timestamp` (parsed, aware), `message.model`, `requestId`->`request_id`, `message.usage`->`Usage`, `isSidechain`, `isMeta`. Session `id`/`cwd`/`git_branch` come from `sessionId`/`cwd`/`gitBranch`; `project` is the cwd basename; `started_at`/`ended_at` are the min/max timestamps; `harness="claude_code"`.
- Split assistant records sharing a `requestId` stay SEPARATE messages (one per record, parent chain intact, same `request_id`). Documented in the module docstring. Usage is copied per record, not summed, because the same usage is repeated on each split.
- Dropped-record content is never stored, including in `metadata`.

## Alternatives rejected
- Merge records by `requestId`: breaks one-uuid-per-message and the parent chain.
- Blocklist of internal types: a new internal type would leak by default.

## Files & interfaces touched
- NEW `pensieve_traces/readers/__init__.py`, `pensieve_traces/readers/claude_code.py`
- NEW `tests/test_claude_code_reader.py`
- NEW `tests/fixtures/claude_code/*.jsonl`
- CHANGED `README.md`, `CHANGELOG.md`

## Test plan
Offline, hand-written synthetic fixtures (fake ids, paths like `/work/demo`, no real data): `plain_chat`, `thinking`, `tool_use_result` (incl. an error result), `split_turn` (shared requestId), `sidechain`, `meta`, `internal_records` (every dropped type, each carrying a unique sentinel string such as a file-history backup). Tests assert the exact message sequence and fields, that no sentinel appears in `session.to_json()`, that unknown types are skipped (caplog), that a malformed line reports its line number, and that `read_project` returns all sessions. A `teich` cross-check compares the user/assistant sequence via `pytest.importorskip("teich")`; `teich` is not added as a dependency.

## Risks
- Privacy: the model holds raw text and tool output, and the reader does no redaction. It must run before any writer. The allowlist keeps file backups out. We never read host session directories; tests use fixtures only.
- Spec conformance: no STS or Hub output here. Fidelity of tool_use/tool_result to the model matters for the later writers.
- Dependencies: none new. `teich` stays an optional test-only import.
- Issue text mentions only record shapes; no instruction conflicts found.

## Open questions
- None

## Size estimate
M
