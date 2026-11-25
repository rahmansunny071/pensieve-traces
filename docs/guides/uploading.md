---
title: "Uploading to the Hub"
description: "Push converted STS traces to a private Hugging Face dataset."
---

# Uploading to the Hub

`pensieve-traces upload` pushes converted traces to a Hugging Face dataset, **private by default**. Uploading is always intentional: `upload` is the only command that contacts the Hub, and `convert` and every other command only write local files. It needs the optional extra and your own login (`hf auth login`); the tool never asks for, stores or prints a token.

```bash
pip install "pensieve-traces[hub]"
pensieve-traces upload traces/ --repo <user>/<dataset>   # private unless --public
```

- `convert` writes the STS header key `pensieveRedacted` (`true` when redaction ran, `false` with `--no-redact`). `upload` refuses any file whose first line lacks `"pensieveRedacted": true` (a missing marker counts as unredacted) unless you pass `--allow-unredacted`. The marker is an attestation, not proof; review traces before sharing.
- Only the top-level `*.jsonl` files of the directory are uploaded, at the repo root.
- If any file has `"harness": "claude.ai"` in its header, `upload` prints a warning with their count before the confirmation prompt (also with `--yes`) saying redaction does not catch names, health details, money amounts or card and bank account numbers and catches only some addresses and phone numbers. It never refuses on this basis; see [Redaction and privacy](../concepts/redaction.md) for the measured coverage.
- A summary (file count, total size, repo, visibility) is shown and you are asked to confirm; `--yes` skips the prompt.
- `--public` must be explicit and prints a warning. If the repo already exists and is public, the upload is refused unless `--public` is given. Without the `hub` extra the command prints an install hint and exits 1.
