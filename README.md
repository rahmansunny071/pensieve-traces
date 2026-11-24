# pensieve-traces

**Turn coding-agent session traces into shareable traces and memory datasets.**

Claude Code (and other agents) record every session as raw JSONL. Those files hold far more than the conversation: file-history backups, full API request blobs, cost state and UI flags, plus whatever secrets, paths and private code passed through the session. pensieve-traces reads them into one canonical session model, redacts them, and writes:

| Output | What it is | Status |
|---|---|---|
| **STS** | Hugging Face [Session Trace Simple Format](https://huggingface.co/docs/hub/main/en/session-traces-format): one JSONL file per session, viewable on the Hub as an [agent trace](https://huggingface.co/docs/hub/main/en/agent-traces) | planned |
| **OpenRouter Broadcast** | One JSON file per model request, with tokens and cost | planned |
| **ConvoMem conversations** | Sessions as `{id, messages[{speaker, text}]}` conversations, one persona per project, ready to be used as history or filler | planned |
| **ConvoMem dataset** | Questions, answers and evidence pointers generated from a project's sessions, in the [ConvoMem](https://huggingface.co/datasets/Salesforce/ConvoMem) layout, runnable with [convomem-arena](https://github.com/rahmansunny071/convomem-arena) | planned |

```
readers                 canonical model                 outputs
claude_code ─┐          Session                       ┌─ STS writer
codex (later)├─────►      messages: id, parent_id,  ──┼─ OpenRouter writer
pi (later)  ─┘            timestamp, role, text,      ├─ ConvoMem conversations
                          reasoning, tool calls,      └─ ConvoMem Q/A builder
                          model, request_id, usage
                        + redaction (on by default)
```

> **Privacy.** Traces can contain prompts, tool output, local paths, secrets, private code and personal data. Redaction is on by default, but review anything before you publish it, and keep datasets private when unsure.

## Redaction

Install the optional extra to enable redaction: `pip install "pensieve-traces[redact]"` (uses [teich](https://pypi.org/project/teich/)'s `TraceAnonymizer`; the base install has no dependencies).

```python
from pensieve_traces import prepare_for_output, redact_session

safe = prepare_for_output(session)  # redacts by default; raises if teich is missing
raw = prepare_for_output(session, redact=False)  # explicit opt-out, emits a UserWarning
copy = redact_session(session)  # scrubbed deep copy, input untouched
```

Writers must obtain their session through `prepare_for_output`. It never falls back to unredacted output: without the extra it raises `RedactionUnavailableError`. Ids, tool names, timestamps and usage are left as is. Redaction is pattern-based and best-effort, so review output before sharing.

## Canonical model

`pensieve_traces.model` defines the harness-neutral dataclasses `Session`, `Message`, `ToolCall`, `Usage` and the `Role` enum (no extra dependencies). Timestamps are timezone-aware, tool arguments stay a raw JSON string, and `Session.to_json()` / `Session.from_json()` round-trip losslessly. The model holds raw text and does no redaction.

```python
from pathlib import Path

from pensieve_traces import Session

session = Session.from_json(Path("session.json").read_text())
session.tool_result_for("call-1")
```

## Reading Claude Code sessions

`pensieve_traces.readers.claude_code` turns raw Claude Code JSONL into canonical sessions (stdlib only). Only the conversation is kept: `user`, `assistant` and `system` records, plus the `ai-title` as `Session.title`. File backups, API request blobs, cost state and every other record type are skipped, and `toolUseResult` is never read.

```python
from pensieve_traces.readers.claude_code import read_project, read_session

session = read_session("path/to/<session>.jsonl")
sessions = read_project("path/to/project-dir")  # *.jsonl; subagent files merge into their session
```

Records sharing a `requestId` stay separate messages; token usage is attached only to the last one so sums are not double-counted. Malformed input raises `ClaudeCodeParseError` with the file path and line number. The reader does no redaction.

## Converting to STS

`pensieve-traces convert` turns a Claude Code session file or project directory into [STS](https://huggingface.co/docs/hub/main/en/session-traces-format) JSONL for the Hugging Face trace viewer, one `<session id>.jsonl` per session (stdlib only):

```bash
pip install "pensieve-traces[redact]"
pensieve-traces convert path/to/<project-dir> --to sts --out traces/
```

- Redaction is on by default. Without the `redact` extra the command exits with an error and writes nothing; pass `--no-redact` to write unredacted output (a warning is printed).
- `--out` must not be the input directory (or inside it), nor the directory of an input file: Claude Code names sessions `<session id>.jsonl`, so writing there would overwrite the originals. The command refuses and writes nothing.
- Sidechain (subagent) messages are skipped; add `--include-sidechains` to keep them.
- Fields STS has no place for are dropped: parent ids, usage, request ids, `is_meta`/`is_error` flags, metadata, `cwd`, `project`, branch and session start/end times. Meta messages are still written.
- From Python: `pensieve_traces.writers.sts.write_session(session, path)` and `session_to_sts_lines(session)` (the latter does not redact).

## Uploading to the Hub

`pensieve-traces upload` pushes converted traces to a Hugging Face dataset, **private by default**. It needs the optional extra and your own login (`hf auth login`); the tool never asks for, stores or prints a token.

```bash
pip install "pensieve-traces[hub]"
pensieve-traces upload traces/ --repo <user>/<dataset>   # private unless --public
```

- `convert` writes the STS header key `pensieveRedacted` (`true` when redaction ran, `false` with `--no-redact`). `upload` refuses any file whose first line lacks `"pensieveRedacted": true` (a missing marker counts as unredacted) unless you pass `--allow-unredacted`. The marker is an attestation, not proof; review traces before sharing.
- Only the top-level `*.jsonl` files of the directory are uploaded, at the repo root.
- A summary (file count, total size, repo, visibility) is shown and you are asked to confirm; `--yes` skips the prompt.
- `--public` must be explicit and prints a warning. If the repo already exists and is public, the upload is refused unless `--public` is given. Without the `hub` extra the command prints an install hint and exits 1.

## How this repo is built: an agent factory

Work happens through an issue-driven factory on Claude Code GitHub Actions. Agents write short designs, the maintainer signs off, then agents build, test and open PRs. Only the maintainer can start or advance work, merge to `main`, or publish anything.

1. Open an issue with the **Factory task** template.
2. Add `factory:design`. The design agent writes `.factory/designs/issue-N.md` and opens a draft design PR.
3. Review it; iterate with `@claude …` comments or edit it yourself.
4. Add `design:approved` to the design PR. The build agent implements it on `factory/issue-N`, runs the checks until green and opens a PR.
5. The review agent checks the PR against the design and `CLAUDE.md`.
6. Merge. Releases stay manual.

| Label | Applied by | Meaning |
|---|---|---|
| `factory:design` | maintainer, on an issue | Ask an agent for a design |
| `factory:designing` | agent | Design in progress / awaiting review |
| `design:approved` | maintainer, on the design PR | Signed off: start building |
| `factory:building` | agent | Implementation in progress |
| `factory:review` | agent | Implementation PR open, awaiting merge |
| `factory:blocked` | agent | Agent hit something it shouldn't decide |

Agent rules live in [`CLAUDE.md`](CLAUDE.md); the stage prompts in [`.claude/commands/`](.claude/commands/).

## Development

```bash
uv sync --locked --extra dev
uvx ruff@0.17.4 format --check . && uvx ruff@0.17.4 check .
uv run --no-sync pytest -q
```

## Author

Created by **[Sajjadur Rahman](https://sajjadur.net/)** · [rahmansunny071@gmail.com](mailto:rahmansunny071@gmail.com)
