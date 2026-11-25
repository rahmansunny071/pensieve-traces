---
title: "Python API"
description: "The library entry points behind the CLI."
---

# Python API

Everything the CLI does is available from Python. Imports are shown on the pages linked below.

| Area | Entry points | Details |
|---|---|---|
| Model | `Session`, `Message`, `ToolCall`, `Usage`, `Role` in `pensieve_traces.model`; `Session.to_json()` / `Session.from_json()` | [Canonical model](../concepts/canonical-model.md) |
| Claude Code reader | `pensieve_traces.readers.claude_code.read_session`, `read_project`; `ClaudeCodeParseError` | [Readers](../concepts/readers.md) |
| claude.ai reader | `pensieve_traces.readers.claude_ai.read_export`; `ClaudeAiParseError` | [Readers](../concepts/readers.md#reading-claudeai-exports) |
| Redaction | `prepare_for_output`, `redact_session`; `RedactionUnavailableError` | [Redaction and privacy](../concepts/redaction.md) |
| STS writer | `pensieve_traces.writers.sts.write_session(session, path)` and `session_to_sts_lines(session)` (the latter does not redact) | [Output formats](../concepts/output-formats.md#sts) |
| ConvoMem conversations | `pensieve_traces.writers.convomem.write_conversations(sessions, out_dir)`, and `sessions_to_conversations(sessions)` / `sessions_to_conversations_with_ids(sessions)` (no redaction); `tool_activity="drop"` omits tool-call summaries | [Output formats](../concepts/output-formats.md#convomem-conversations) |
| ConvoMem builder | `pensieve_traces.builders.convomem`: `extract_candidates`, `build_items`, `dataset` | [Build a ConvoMem dataset](../getting-started/convomem-dataset.md) |
| Upload | `pensieve_traces.upload.upload_files` (needs the `hub` extra; `collect_files`, `check_redaction` and `count_harness` are the checks the CLI runs first) | [Uploading to the Hub](../guides/uploading.md) |
| Dataset check | `pensieve_traces.builders.convomem.check_dataset(path)` returns a list of `Problem(file, item, reason)` | [Checking](../guides/checking-convomem.md) |

Writers must obtain their session through `prepare_for_output`; the lower-level functions marked "no redaction" do not redact.
