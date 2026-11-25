# Design: claude.ai export reader (#46)

## Problem
Users with claude.ai chats cannot feed them to the STS and ConvoMem writers; only Claude Code sessions are readable. `read_export(path)` should turn an export's `conversations.json` into canonical `Session`s, one per conversation, keeping private data (account ids, attachment text) out by default.

## Proposed approach
New stdlib-only `pensieve_traces/readers/claude_ai.py`, no redaction, no model changes. Adopts all four issue recommendations.
- `read_export(path, *, include_attachments=False) -> list[Session]`: `path` is a directory (reads only `<dir>/conversations.json`) or a `conversations.json` file. Nothing else in the export is opened.
- `ClaudeAiParseError(ValueError)` with `path` and `location` (e.g. `conversations[3].chat_messages[7]`); raised for non-array JSON, non-object entries, missing required keys (`uuid`, `chat_messages`, message `uuid`/`sender`/`created_at`), unknown `sender`, bad or naive timestamps, bad JSON/UTF-8. Unknown block types become a `[unknown block: <type>]` placeholder (type name only).
- Session: `id=uuid`, `title=name`, `harness="claude.ai"`, `started_at=created_at`, `ended_at=updated_at`. `account` is never read. `summary` is not read.
- Text comes from `content` blocks, never top-level `text`. Human `text` becomes USER; `injected_prompt_block` becomes USER with `is_meta=True` (prompt text); `document`/`image` and `files[]` become `[attached: <title or file_name>]` / `[image]` placeholders in the user text. `attachments[]` become `[attached: name]`; with `include_attachments=True` the `extracted_content` is appended.
- Assistant message split in block order: a run of thinking/text/tool_use blocks accumulates into one ASSISTANT message (`reasoning` joined thinking, `text` joined text, `tool_calls` with `arguments=json.dumps(input)`); each `tool_result` becomes a `Role.TOOL` message (`tool_call_id`, `is_error`) and closes the run; later blocks start a new ASSISTANT message. Ids: first derived message `<uuid>`, others `<uuid>#<block index>`; each parented to the previous derived message, the first to the message's parent (sentinel `00000000-0000-4000-8000-000000000000` becomes `None`). Derived messages share the source `created_at`.
- `tool_result.content` flattening: `text` as is; `knowledge` as `[web results: <titles>]`; `image_gallery` as `[images: N]`; `local_resource` as `[local resource: <name>]`; others as `[unknown content: <type>]`. Never raw JSON.
- Branches: keep one path: the leaf with the latest `created_at` (ties: later file order) and its ancestors (this selects the root too). Dropped message count goes to `Session.metadata["dropped_branch_messages"]`. A parent missing from the file is treated as a root; a parent cycle raises `ClaudeAiParseError`.

## Alternatives rejected
- Keep all branches with `parent_id`: both writers ignore `parent_id` and would repeat edited turns and duplicate evidence.
- Raw JSON for non-text tool results: floods traces with URLs and payloads.

## Files & interfaces touched
- NEW `pensieve_traces/readers/claude_ai.py` (`read_export`, `ClaudeAiParseError`; module docstring documents the id/parent contract).
- NEW `tests/test_claude_ai_reader.py`; NEW `tests/fixtures/claude_ai/` synthetic exports.
- CHANGED `README.md`: new section "Reading claude.ai exports"; intro and readers diagram name both sources (`claude_ai` in the diagram); status table fixes STS to "available (`convert --to sts`)" (Closes #44; Refs #50, README part only, CLAUDE.md part left for the orchestrator).
- CHANGED `CHANGELOG.md`: one entry.
- No dependency, version, CLI or workflow changes. `readers/__init__.py` unchanged.

## Test plan
Offline, hand-written synthetic fixtures: plain chat; interleaved thinking/tool_use/tool_result/text turn (ids, parent chain, `is_error`, `tool_call_id`); branched conversation incl. two roots (path choice, dropped count); attachment (placeholder default, inlined with flag); injected prompt block (`is_meta`); each non-text tool_result type; unknown block type; top-level `text` disagreeing with content. Errors: non-array, missing key, unknown sender, naive timestamp, cycle (path and location asserted). A fixture account uuid must not appear in `Session.to_json()`. Round-trip through `session_to_sts_lines` and `sessions_to_conversations` without error.

## Risks
- Privacy: attachment text excluded by default; `account`, `summary`, manifest and `light_metadata` never read. Thinking and tool output can still hold private data, so the redaction pass stays the caller's job (writers already require it). Fixtures are synthetic only.
- Spec conformance: no writer changes; STS header gets `harness: "claude.ai"` (a free string in the writer today).
- Dependencies: none.
- The issue text is data; nothing in it asked to change process.

## Open questions
- None blocking. Unverified from code or spec: whether the STS viewer treats an unfamiliar `harness` string specially; the writer already passes any string through.

## Size estimate
M
