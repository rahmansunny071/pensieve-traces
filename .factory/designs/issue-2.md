# Design: Claude Code reader: raw session JSONL to canonical Session (#2)

## Problem
Users have Claude Code session files (`<session>.jsonl`) full of internal bookkeeping (file backups, API request blobs, cost state) mixed with the real conversation. There is no way yet to load one into the canonical `Session` model. The reader must return only the conversation, so nothing internal can leak into later output.

## Proposed approach
New `pensieve_traces/readers/claude_code.py`, stdlib only.

**API**
- `read_session(path) -> Session` streams the file line by line. Blank lines are skipped. An empty file returns a Session with no messages.
- `read_project(dir) -> list[Session]` reads each `*.jsonl` directly in the directory (non-recursive). Files are read in sorted filename order. Files sharing a `sessionId` (e.g. subagent `agent-*.jsonl`) merge into one Session, each message keeping its `is_sidechain` flag, so ids are unique. Session-level fields (cwd, git branch, title) come from the main file (stem equals `sessionId`), else the first in sorted order. Messages use a stable sort on `(timestamp is None, timestamp)`: file order breaks ties, untimestamped go last. Repeated uuids across files are skipped with a debug log; `started_at`/`ended_at` are recomputed after merging. Sessions sort by `(started_at is None, started_at, id)`.

**Errors**
- `ClaudeCodeParseError(ValueError)` carries path and line number. Raised for bad JSON, non-object lines, and kept records with the wrong structure (never a bare KeyError/TypeError).
- Duplicate uuids are skipped with a debug log.

**Filtering**
- Allowlist: only `user`, `assistant`, `system` records become messages. Everything else is skipped with `logger.debug`. `ai-title` sets `Session.title` from the record's top-level `aiTitle` string (last wins).
- Kept records are read only through the listed fields. Top-level `toolUseResult` (can hold full file contents) is ignored. Dropped-record content is never stored, including in `metadata`.

**Content shapes**
- `message.content` is a string or a list of blocks.
- Multiple `text` or `thinking` blocks join with "\n\n".
- Assistant: `text` -> `text`, `thinking` -> `reasoning`, `tool_use` -> `ToolCall(id, name, json.dumps(input))`.
- User: `text` -> `user` message; each `tool_result` -> its own `tool` message (`tool_call_id`, `is_error`, list content flattened to text). Image blocks become `[image]`. `redacted_thinking` and unknown blocks are skipped.
- `system` records take text from top-level `content` (not `message.content`); `subtype` goes in `Message.metadata`.

**Ids and parents**
- A record yielding several messages emits them in block order, chained: the last gets the record's `uuid`, earlier ones `uuid#i` (i = 0, 1, ...), each parented to the previous, the first to `parentUuid`.

**Mapping**
- `timestamp` (parsed, aware), `message.model`, `requestId`->`request_id`, `isSidechain`, `isMeta`.
- Session id: `sessionId`, else file stem. `cwd` and git branch: first value seen. `project` is the cwd basename. `started_at`/`ended_at` are min/max timestamps. `harness="claude_code"`.

**Split turns and usage**
- Assistant records sharing a `requestId` stay separate messages (same `request_id`, parent chain intact).
- Usage keys map `input_tokens`->`input_tokens`, `output_tokens`->`output_tokens`, `cache_read_input_tokens`->`cache_read_tokens`, `cache_creation_input_tokens`->`cache_creation_tokens`; missing or null become 0, other keys are ignored. The reader builds `Usage` from this mapping and never calls `Usage.from_dict` on the raw dict.
- `message.usage` goes only on the last record of each `requestId` group; others get none. Contract downstream writers rely on: summing usage across messages gives the per-request total.

## Alternatives rejected
- Merge records by `requestId`: breaks one-uuid-per-message and the parent chain.
- Blocklist of internal types: a new internal type would leak by default.

## Files & interfaces touched
- NEW `pensieve_traces/readers/__init__.py`, `pensieve_traces/readers/claude_code.py`
- NEW `tests/test_claude_code_reader.py`
- NEW `tests/fixtures/claude_code/*.jsonl`
- CHANGED `README.md`, `CHANGELOG.md`

## Test plan
Offline, hand-written synthetic fixtures (fake ids, paths like `/work/demo`). Fixtures: `plain_chat`, `thinking`, `tool_use_result` (error result, parallel results), `split_turn`, `sidechain`, `meta`, `string_content`, `system`, `image`, `blank_truncated`, `dup_uuid`, `empty`, `internal_records` (each dropped type with a unique sentinel, plus a sentinel in `toolUseResult`).
- Assert exact message sequence, ids and parents, fields.
- No sentinel in `session.to_json()`, except `ai-title`: its `aiTitle` value must equal `session.title`, and a sentinel in any other field of that record must not appear.
- Unknown types skipped (caplog on the reader's logger name).
- Malformed and truncated lines raise with path and line number.
- Usage only on the last record of a request group; a fixture with non-zero cache tokens asserts the key mapping.
- `read_project`: merged sessions, unique ids; session fields come from the main file (and from the first sorted file when none is main); equal timestamps keep file order, untimestamped messages last; cross-file duplicate uuids skipped; `started_at`/`ended_at` recomputed; sessions sorted by `(started_at is None, started_at, id)`.
- `teich` cross-check via `pytest.importorskip("teich")`: merge assistant messages sharing a `request_id`, compare the `(role, text)` sequence, falling back to roles only. `teich` is not a dependency.

## Risks
- Privacy: the model holds raw text and tool output; no redaction here. It must run before any writer. The allowlist and field-only reads keep file backups and `toolUseResult` out. Tests use fixtures only.
- Spec conformance: no STS or Hub output here. tool_use/tool_result fidelity matters for later writers.
- Usage double-counting: writers must not sum usage across split records; the last-record placement prevents it, and tests pin it.
- Id collisions: `uuid#i` could clash with a real uuid, and merged files may repeat uuids; duplicates are skipped and logged.
- One malformed file aborts `read_project`: errors propagate with path and line; fail-fast is intended.
- Dependencies: none new. Issue text mentions only record shapes; no instruction conflicts found.

## Open questions
- None

## Size estimate
M
