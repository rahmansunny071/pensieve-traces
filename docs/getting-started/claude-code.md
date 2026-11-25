---
title: "Convert Claude Code sessions"
description: "Turn a Claude Code session file or project directory into STS JSONL or ConvoMem conversations."
---

# Convert Claude Code sessions

`pensieve-traces convert` turns a Claude Code session file or project directory, or a claude.ai export, into [STS](https://huggingface.co/docs/hub/main/en/session-traces-format) JSONL for the Hugging Face trace viewer, one `<session id>.jsonl` per session (stdlib only):

```bash
pip install "pensieve-traces[redact]"
pensieve-traces convert path/to/<project-dir> --to sts --out traces/
```

- Input is detected: a directory of `*.jsonl` files or one session file is Claude Code. A directory with `conversations.json` and no `*.jsonl` (or a file named `conversations.json`) is a [claude.ai export](claude-ai.md). A directory holding both is refused as ambiguous, and one holding neither is an error. `--from claude-code` or `--from claude-ai` forces the reader.
- Redaction is on by default. Without the `redact` extra the command exits with an error and writes nothing; pass `--no-redact` to write unredacted output (a warning is printed).
- `--out` must not be the input directory (or inside it), nor the directory of an input file (for an export, its directory): Claude Code names sessions `<session id>.jsonl`, so writing there would overwrite the originals. The command refuses and writes nothing.
- Sidechain (subagent) messages are skipped; add `--include-sidechains` to keep them.
- Fields STS has no place for are dropped: parent ids, usage, request ids, `is_meta`/`is_error` flags, metadata, `cwd`, `project`, branch and session start/end times. Meta messages are still written.

For ConvoMem conversations, use `--to convomem-conversations` (see [Output formats](../concepts/output-formats.md)). From Python, see the [Python API](../reference/python-api.md).
