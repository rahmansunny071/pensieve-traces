---
title: "CLI"
description: "Every command and flag, as printed by --help."
---

# CLI

`pensieve-traces` has four commands. The text below is each command's `--help`.

| Command | What it does | Guide |
|---|---|---|
| `convert` | Convert session traces to STS or ConvoMem conversations | [Claude Code](../getting-started/claude-code.md), [claude.ai](../getting-started/claude-ai.md) |
| `build convomem` | Build a ConvoMem dataset with a model | [Build a ConvoMem dataset](../getting-started/convomem-dataset.md) |
| `check-convomem` | Check the structure of a generated dataset (reads only the directory) | [Checking](../guides/checking-convomem.md) |
| `upload` | Upload converted traces to a Hugging Face dataset (the only command that contacts the Hub) | [Uploading](../guides/uploading.md) |

Redaction is on by default for `convert` and `build convomem`; `--no-redact` turns it off with a warning.

## `pensieve-traces --help`

```text
usage: pensieve-traces [-h] {convert,build,check-convomem,upload} ...

positional arguments:
  {convert,build,check-convomem,upload}
    convert             convert session traces to a shareable format
    build               build a dataset from session traces
    check-convomem      check the structure of a generated ConvoMem dataset
                        (reads only DIR)
    upload              upload converted traces to a Hugging Face dataset

options:
  -h, --help            show this help message and exit
```

## `pensieve-traces convert --help`

```text
usage: pensieve-traces convert [-h] [--from {claude-code,claude-ai}]
                               --to {convomem-conversations,sts} --out OUT
                               [--no-redact] [--include-sidechains]
                               input

positional arguments:
  input                 a Claude Code session .jsonl file or project
                        directory, or a claude.ai export directory or
                        conversations.json

options:
  -h, --help            show this help message and exit
  --from {claude-code,claude-ai}
                        input source (default: detected from the input)
  --to {convomem-conversations,sts}
                        output format
  --out OUT             output directory (sts: one file per session; convomem-
                        conversations: two files)
  --no-redact           write unredacted output (redaction is the default)
  --include-sidechains  include subagent (sidechain) messages
```

## `pensieve-traces build convomem --help`

```text
usage: pensieve-traces build convomem [-h] --out OUT --model MODEL
                                      [--base-url BASE_URL]
                                      [--api-key-env API_KEY_ENV]
                                      [--allow-remote-model] [--topics TOPICS]
                                      [--cache-dir CACHE_DIR]
                                      [--from {claude-code,claude-ai}]
                                      [--no-redact]
                                      input [input ...]

positional arguments:
  input                 Claude Code session files or project directories,
                        claude.ai exports, in any mix

options:
  -h, --help            show this help message and exit
  --out OUT             dataset directory (core_benchmark/ inside)
  --model MODEL         chat model name at the endpoint
  --base-url BASE_URL   OpenAI-compatible endpoint (default
                        http://localhost:11434/v1)
  --api-key-env API_KEY_ENV
                        name of the environment variable holding the API key
  --allow-remote-model  allow a --base-url that is not localhost (your text is
                        sent there)
  --topics TOPICS       JSON file mapping topic names to id/project/title
                        matchers
  --cache-dir CACHE_DIR
                        model reply cache (default: <out>.cache, beside the
                        dataset)
  --from {claude-code,claude-ai}
                        input source for every input (default: detected per
                        input)
  --no-redact           send and write unredacted text (redaction is the
                        default)
```

## `pensieve-traces check-convomem --help`

```text
usage: pensieve-traces check-convomem [-h] dir

positional arguments:
  dir         dataset directory (core_benchmark/ inside)

options:
  -h, --help  show this help message and exit
```

## `pensieve-traces upload --help`

```text
usage: pensieve-traces upload [-h] --repo REPO [--public] [--allow-unredacted]
                              [--yes]
                              input

positional arguments:
  input               directory of converted *.jsonl files

options:
  -h, --help          show this help message and exit
  --repo REPO         dataset repo id, <user>/<name>
  --public            make the dataset public (default: private)
  --allow-unredacted  upload files not marked as redacted
  --yes               skip the confirmation prompt
```

