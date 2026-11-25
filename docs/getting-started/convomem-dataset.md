---
title: "Build a ConvoMem dataset"
description: "Generate a ConvoMem-style memory dataset about you from Claude Code sessions and claude.ai chats."
---

# Build a ConvoMem dataset

`pensieve-traces build convomem` turns Claude Code sessions and claude.ai chats, together, into a [ConvoMem](https://huggingface.co/datasets/Salesforce/ConvoMem)-style memory dataset about you (one persona, `me`): a model finds facts in your conversations and writes questions and answers across ConvoMem's six categories, and code checks that the evidence is verbatim.

```bash
pip install "pensieve-traces[redact]"
pensieve-traces build convomem path/to/<project-dir> path/to/claude-export \
  --out dataset/ --model qwen3:14b [--topics topics.json]
```

- **Inputs:** one or more, in any mix (a Claude Code project directory or session file, a claude.ai export directory or `conversations.json`), detected as for `convert` (`--from` forces one reader for all inputs). Sessions with the same id across inputs are read once (the first wins).
- **Model endpoint:** any OpenAI-compatible server, by default a local Ollama (`--base-url http://localhost:11434/v1`). `--api-key-env NAME` names the environment variable holding a key (never a flag; the command fails early if it is unset). A `--base-url` that is not `localhost`, `127.0.0.1` or `[::1]` is refused unless `--allow-remote-model` is given, and a URL with embedded credentials is always refused. Before the first model call the command prints which endpoint receives the text. The command never contacts the Hub; uploading stays a separate, explicit `upload` run.
- **Redaction** is on by default and runs once, with one anonymizer, over all inputs before any text reaches the model, so placeholders are consistent across conversations, evidence and filler. It is best-effort: see [Redaction and privacy](../concepts/redaction.md) for what it misses (names, health details and money amounts in chats, for example). Without the `redact` extra the command exits with an error and writes nothing; `--no-redact` sends and writes unredacted text and prints a warning. Sessions are redacted in input order, so adding or reordering inputs can change placeholders and miss part of the cache.
- **Output:** see [Output formats](../concepts/output-formats.md#convomem-dataset-layout).
- **Topics (optional):** see [Topics](../guides/topics.md).
- **Where things go:** the dataset is written to a temporary directory beside `--out` and moved into place, so a failed run leaves nothing half-written. The command refuses an `--out` that is, or is inside, an input, that is a file, or that already holds `core_benchmark/` or `build_manifest.json`, and it never replaces an existing file. The model reply cache lives beside the dataset in `<out>.cache/` (or `--cache-dir`), so sharing the dataset folder never ships model outputs; the cache directory is refused inside an input or inside `--out`. A rerun reuses the cache.

!!! warning "Privacy"
    The dataset is about you: it holds facts from your conversations. Review it before sharing, keep it private, and remember that redaction is pattern-based (see the table in [Redaction and privacy](../concepts/redaction.md)). The cache holds model replies about your conversations too.

Then [check the dataset](../guides/checking-convomem.md). From Python: `pensieve_traces.builders.convomem` (`extract_candidates`, `build_items`) and `dataset`; see the [Python API](../reference/python-api.md).
