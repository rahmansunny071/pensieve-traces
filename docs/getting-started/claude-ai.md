---
title: "Convert a claude.ai export"
description: "Convert a claude.ai data export into STS or ConvoMem conversations."
---

# Convert a claude.ai export

Export your chats from claude.ai (Settings, Privacy, Export data), then point `convert` at the export directory or its `conversations.json`:

```bash
pip install "pensieve-traces[redact] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"
pensieve-traces convert path/to/claude-export --to sts --out traces/
pensieve-traces convert path/to/claude-export --to convomem-conversations --out convomem/
```

Input is detected as for [Claude Code sessions](claude-code.md); `--from claude-ai` forces the claude.ai reader. Exports write one `<conversation uuid>.jsonl` per conversation with `harness: "claude.ai"` in the header; attachment text is left out. A claude.ai export gives one ConvoMem conversation per chat. The Hub's rendering of the `claude.ai` harness id is unverified (STS only lists `llama.app`).

`--out` equal to the export directory, inside it, or equal to the directory of a `conversations.json` input is refused, as for Claude Code input. What the reader keeps and drops is described under [Readers](../concepts/readers.md#reading-claudeai-exports).

!!! warning "Chats hold personal data"
    Redaction does not catch chat personal data in general (names, health details, money amounts, card numbers and more). See [Redaction and privacy](../concepts/redaction.md) and review claude.ai traces before sharing them.
