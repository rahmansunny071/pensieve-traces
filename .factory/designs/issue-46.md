# Design: claude.ai export reader (#46)

## Problem
Users with claude.ai chats cannot feed them to the STS and ConvoMem writers; only Claude Code sessions are readable. `read_export(path)` should turn an export's `conversations.json` into canonical `Session`s, one per conversation, keeping private data (account ids, attachment text) out by default.

## Proposed approach
New stdlib-only `pensieve_traces/readers/claude_ai.py`, no redaction, no model changes. Adopts all four issue recommendations.
- `read_export(path, *, include_attachments=False) -> list[Session]`: `path` is a directory (reads only `<dir>/conversations.json`) or a `conversations.json` file. Nothing else in the export is opened. Sessions are returned in file order. Module constant `HARNESS = "claude.ai"`.
- `ClaudeAiParseError(ValueError)` with `path` and `location` (e.g. `conversations[3].chat_messages[7]`); raised for bad JSON/UTF-8, non-array JSON, non-object entries, missing required keys (`uuid`, `chat_messages`; message `uuid`, `sender`, `created_at`), unknown `sender`, bad or naive timestamps, a repeated conversation or message uuid, and parent cycles anywhere in the graph (self-parent included).
- Session: `id=uuid`, `title=name` (empty gives `None`), `harness=HARNESS`, `started_at=created_at`, `ended_at=updated_at`. `account` and `summary` are never read. Text comes from `content` blocks, never the top-level `text`.
- Human message: each `injected_prompt_block` becomes its own `is_meta=True` USER message (its `prompt`). `text`, `document` (`[attached: <title>]`), `image` (`[image]`) and unknown blocks merge, in block order, into one non-meta USER message; placeholders for `files[]` and `attachments[]` (`[attached: <file_name>]`) go into that message (created if needed). With `include_attachments=True` an attachment's `extracted_content` follows its placeholder.
- Assistant message: consecutive thinking/text/tool_use blocks accumulate into one ASSISTANT message (`reasoning` joined thinking, `text` joined text, `ToolCall(id, name, json.dumps(input))`); each `tool_result` becomes a `Role.TOOL` message (`tool_call_id`, `is_error`, null is False) and closes the run; later blocks open a new ASSISTANT message. Wrong-sender and unknown blocks become `[unknown block: <type>]`. `tool_use.id`/`name` and `tool_result.tool_use_id` must be strings (else parse error); null `thinking` adds nothing.
- `tool_result.content` flattening: `text` as is; `knowledge` as `[web results: <titles>]`; `image_gallery` as `[images: N]`; `local_resource` as `[local resource: <name>]`; others `[unknown content: <type>]`. Never raw JSON.
- Id/parent rule (same as the Claude Code reader), for every source message split into several canonical messages: the LAST derived message gets the bare `<uuid>`, earlier ones `<uuid>#<i>` (`i` = position among the derived messages; the issue said block index, same uniqueness), chained by `parent_id`, the first to the source parent. So the next source message's `parent_message_uuid` points at the last derived piece. The sentinel parent becomes `None`; a parent absent from the file is a root. A source message yielding nothing is skipped and its child links to the nearest kept ancestor. Derived messages share the source `created_at`. Documented in the module docstring.
- Branches: keep one path, the leaf with the latest `created_at` (ties: later file order) and its ancestors. `Session.metadata["dropped_branch_messages"]` counts dropped source messages.

## Alternatives rejected
- Keep all branches with `parent_id`: both writers ignore `parent_id` and would repeat edited turns and duplicate evidence.
- Raw JSON for non-text tool results: floods traces with URLs and payloads.

## Files & interfaces touched
- NEW `pensieve_traces/readers/claude_ai.py` (`read_export`, `ClaudeAiParseError`, `HARNESS`).
- NEW `tests/test_claude_ai_reader.py`; NEW `tests/fixtures/claude_ai/`.
- CHANGED `README.md`: section "Reading claude.ai exports" (does not imply `convert` accepts exports; that is #47); intro and readers diagram name both sources (`claude_ai`); STS status row fixed to available (Closes #44; Refs #50, README part only).
- CHANGED `CHANGELOG.md`: one entry.
- No dependency, version, CLI or workflow changes.

## Test plan
Offline, synthetic fixtures: plain chat; interleaved thinking/tool_use/tool_result/text turn (ids, parent chain, `is_error`, `tool_call_id`); the next human message parented to the last derived message of the previous assistant turn; branched conversation incl. two roots (path choice, dropped count); injected block followed by text (meta then non-meta); attachment (placeholder default, inlined with flag); each non-text tool_result type; unknown block; top-level `text` disagreeing with content. Directory and `conversations.json` inputs give equal sessions; the directory fixture holds decoy `manifest-*.json` and `memories/` files with a sentinel string that must not appear in any `to_json()`; account uuid likewise; `started_at`, `ended_at` and all message timestamps timezone-aware. Errors (path and location asserted): non-array, missing key, unknown sender, naive timestamp, cycle, self-parent, duplicate uuid. Round-trip through `session_to_sts_lines` and `sessions_to_conversations`.

## Risks
- Privacy: `injected_prompt_block` text (e.g. the memory header) is kept and reaches STS output, as do thinking and tool output, so the redaction pass remains required before sharing. Attachment text excluded by default; `account`, `summary`, manifest, `light_metadata` never read. Fixtures synthetic only.
- Spec conformance: no writer changes; STS header `harness` becomes `"claude.ai"` (free string in the writer).
- Dependencies: none.

## Open questions
None blocking. Unverified: whether the STS viewer special-cases an unfamiliar `harness` string.

## Size estimate
M
