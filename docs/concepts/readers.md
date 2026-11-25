---
title: "Readers"
description: "How Claude Code sessions and claude.ai exports become canonical sessions."
---

# Readers

## Reading Claude Code sessions

`pensieve_traces.readers.claude_code` turns raw Claude Code JSONL into canonical sessions (stdlib only). Only the conversation is kept: `user`, `assistant` and `system` records, plus the `ai-title` as `Session.title`. File backups, API request blobs, cost state and every other record type are skipped, and `toolUseResult` is never read.

```python
from pensieve_traces.readers.claude_code import read_project, read_session

session = read_session("path/to/<session>.jsonl")
sessions = read_project("path/to/project-dir")  # *.jsonl; subagent files merge into their session
```

Records sharing a `requestId` stay separate messages; token usage is attached only to the last one so sums are not double-counted. Malformed input raises `ClaudeCodeParseError` with the file path and line number. The reader does no redaction.

## Reading claude.ai exports

`pensieve_traces.readers.claude_ai` turns a claude.ai data export (Settings, Privacy, Export data) into canonical sessions, one per conversation (stdlib only). It reads only `conversations.json`; the account block, memories, projects, artifacts, `light_metadata` and the manifest (which holds signed download URLs) are never opened. `pensieve-traces convert` accepts exports too (see [Convert a claude.ai export](../getting-started/claude-ai.md)).

```python
from pensieve_traces.readers.claude_ai import read_export

sessions = read_export("path/to/export-dir")  # or path/to/conversations.json
```

- Sessions have `harness="claude.ai"` and come back in file order. There is no model name or token usage in the export.
- Text is built from the `content` blocks (the top-level `text` is unreliable). A human turn becomes a user message; each `injected_prompt_block` (system notes such as the memory header) becomes its own `is_meta` message. An assistant turn with interleaved thinking, tool calls and results is split, in order, into assistant and tool messages.
- Branches (edited or regenerated turns): only the path to the latest message is kept, and `Session.metadata["dropped_branch_messages"]` counts the rest.
- Attached files and images become placeholders such as `[attached: name.pdf]`. Attachment text is dropped unless you pass `include_attachments=True`. Web results, image galleries and local resources in tool output are summarised in one line, not copied as JSON.
- Malformed input raises `ClaudeAiParseError` with the file path and a location such as `conversations[3].chat_messages[7]`. The reader does no redaction, and injected prompt text, thinking and tool output stay in the sessions, so redact before sharing.
