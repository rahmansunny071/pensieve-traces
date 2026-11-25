---
title: "Limitations"
description: "What pensieve-traces does not do or cannot verify."
---

# Limitations

- **Redaction is best-effort.** It is pattern-based and misses names, health details, money amounts, card numbers, IBANs and undashed SSN-style numbers in chats, and some addresses and phone numbers. See the [measured coverage](../concepts/redaction.md#what-redaction-covers-on-chat-content).
- **Email followed by a period and then text** with no space (`name@host.invalid.I will`) is not covered.
- **The Hub's rendering of the `claude.ai` harness id is unverified** (STS only lists `llama.app`).
- **OpenRouter Broadcast** output is planned, not available. Other agent readers (codex, pi) are later.
- **ConvoMem conversations and datasets** are not loadable by convomem-arena from a local folder yet: that needs [convomem-arena#18](https://github.com/rahmansunny071/convomem-arena/issues/18). `check-convomem` checks structure only; content checks belong to convomem-arena's data check.
- **Fields STS has no place for are dropped** (parent ids, usage, request ids, flags, metadata, `cwd`, `project`, branch, session times).
- **The `pensieveRedacted` marker is an attestation, not proof.**
- **Branches in claude.ai chats:** only the path to the latest message is kept.
