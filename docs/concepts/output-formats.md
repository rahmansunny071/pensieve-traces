---
title: "Output formats"
description: "STS, ConvoMem conversations and the ConvoMem dataset layout."
---

# Output formats

## STS

[STS](https://huggingface.co/docs/hub/main/en/session-traces-format) is the Hugging Face Session Trace Simple Format: one JSONL file per session, named `<session id>.jsonl`, viewable on the Hub as an [agent trace](https://huggingface.co/docs/hub/main/en/agent-traces). Write it with `pensieve-traces convert --to sts` ([Claude Code](../getting-started/claude-code.md), [claude.ai](../getting-started/claude-ai.md)); the options and the fields STS has no place for are listed there. `convert` writes the header key `pensieveRedacted` (`true` when redaction ran, `false` with `--no-redact`), which [`upload`](../guides/uploading.md) checks. Exports from claude.ai carry `harness: "claude.ai"` in the header.

## ConvoMem conversations

`pensieve-traces convert --to convomem-conversations` exports a project's sessions as [ConvoMem](https://huggingface.co/datasets/Salesforce/ConvoMem)-style conversations (one session per conversation, ordered by start time), in the filler-file layout [convomem-arena](https://github.com/rahmansunny071/convomem-arena) reads (loading them from a local folder needs [convomem-arena#18](https://github.com/rahmansunny071/convomem-arena/issues/18)):

```bash
pensieve-traces convert path/to/<project-dir> --to convomem-conversations --out convomem/
pensieve-traces convert path/to/claude-export --to convomem-conversations --out convomem/
```

Input is detected as for STS (a claude.ai export gives one conversation per chat; `--from` overrides).

`--out` is a directory and receives two files:

- `conversations.json`: `{"evidence_items": [{"conversations": [{"id", "containsEvidence", "messages": [{"speaker", "text"}]}]}]}` (the filler-file layout; `id` is the session id, speakers are `User` / `Assistant`).
- `conversations.message_ids.json`: `{conversation id: [canonical message id, ...]}`, index-aligned with each conversation's `messages`, so Q/A generation can point evidence at exact messages.

Only user prompts and assistant prose are kept; meta, sidechain, system and tool-result messages and empty turns are dropped. Assistant tool calls become one-line `[ran Bash: git status]` summaries (from Python, `tool_activity="drop"` omits them). Redaction is on by default, as for STS, and one anonymizer covers all sessions. The command refuses (writing nothing) if either output file already exists or `--out` would overwrite the input sessions. From Python: see the [Python API](../reference/python-api.md).

## ConvoMem dataset layout

[`build convomem`](../getting-started/convomem-dataset.md) writes, in `--out`:

- `core_benchmark/evidence_questions/<category folder>/<N>_evidence/me.json`, with folders `user_evidence`, `assistant_facts_evidence`, `changing_evidence`, `abstention_evidence`, `preference_evidence`, `implicit_connection_evidence` (folders without items are not created). Each item has `question`, `answer`, `message_evidences` and `conversations` (all `containsEvidence: true`).
- `core_benchmark/filler_conversations/me.json`: the conversations that are evidence for no item. convomem-arena's mixer leaves an item's own evidence out of its filler, but its data check does not, so keeping evidence conversations in the filler file would make most items flag there. The cost is a smaller filler pool.
- `build_manifest.json`: input and conversation counts (no paths), model name, prompt hashes, whether redaction was on, per-category item counts and the drop counts of each step. No base URL, key or timestamp.

[Check the structure](../guides/checking-convomem.md) with `check-convomem`.
