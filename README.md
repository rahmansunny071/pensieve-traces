# pensieve-traces

**Turn coding-agent session traces and claude.ai chat exports into shareable traces and memory datasets.**

Claude Code (and other agents) record every session as raw JSONL. Those files hold far more than the conversation: file-history backups, full API request blobs, cost state and UI flags, plus whatever secrets, paths and private code passed through the session. claude.ai chats can be exported as a data export (`conversations.json`). pensieve-traces reads both into one canonical session model, redacts them, and writes:

| Output | What it is | Status |
|---|---|---|
| **STS** | Hugging Face [Session Trace Simple Format](https://huggingface.co/docs/hub/main/en/session-traces-format): one JSONL file per session, viewable on the Hub as an [agent trace](https://huggingface.co/docs/hub/main/en/agent-traces) | available (`convert --to sts`) |
| **OpenRouter Broadcast** | One JSON file per model request, with tokens and cost | planned |
| **ConvoMem conversations** | Sessions as `{id, messages[{speaker, text}]}` conversations, one persona per project, ready to be used as history or filler | available (`convert --to convomem-conversations`) |
| **ConvoMem dataset** | Questions, answers and evidence pointers generated from a project's sessions, in the [ConvoMem](https://huggingface.co/datasets/Salesforce/ConvoMem) layout, runnable with [convomem-arena](https://github.com/rahmansunny071/convomem-arena) | planned |

```
readers                 canonical model                 outputs
claude_code ─┐          Session                       ┌─ STS writer
claude_ai   ─┤
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

**What redaction covers on chat content.** teich was tuned on coding sessions. Measured with teich 0.3.8 (the version `uv.lock` pins) on a synthetic claude.ai-style chat (`tests/fixtures/redact/chat_pii_session.json`), pinned by `tests/test_redact_chat_coverage.py` so a teich upgrade that changes coverage shows up as a test change:

| Kind | Replaced |
|---|---|
| email address | yes |
| SSN-style number | yes |
| street address (the match also swallows the rest of its sentence) | yes |
| 10-digit phone after a phrase like "Call me at" | yes |
| 7-digit phone after other phrasing ("My phone is 555-0143") | no |
| IBAN | no |
| card number | no |
| person name | no |
| health detail | no |
| money amount | no |

Phone matching depends on the surrounding phrasing. Redaction does not catch chat personal data in general: review claude.ai traces before sharing them.

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

## Reading claude.ai exports

`pensieve_traces.readers.claude_ai` turns a claude.ai data export (Settings, Privacy, Export data) into canonical sessions, one per conversation (stdlib only). It reads only `conversations.json`; the account block, memories, projects, artifacts, `light_metadata` and the manifest (which holds signed download URLs) are never opened. `pensieve-traces convert` accepts exports too (see below).

```python
from pensieve_traces.readers.claude_ai import read_export

sessions = read_export("path/to/export-dir")  # or path/to/conversations.json
```

- Sessions have `harness="claude.ai"` and come back in file order. There is no model name or token usage in the export.
- Text is built from the `content` blocks (the top-level `text` is unreliable). A human turn becomes a user message; each `injected_prompt_block` (system notes such as the memory header) becomes its own `is_meta` message. An assistant turn with interleaved thinking, tool calls and results is split, in order, into assistant and tool messages.
- Branches (edited or regenerated turns): only the path to the latest message is kept, and `Session.metadata["dropped_branch_messages"]` counts the rest.
- Attached files and images become placeholders such as `[attached: name.pdf]`. Attachment text is dropped unless you pass `include_attachments=True`. Web results, image galleries and local resources in tool output are summarised in one line, not copied as JSON.
- Malformed input raises `ClaudeAiParseError` with the file path and a location such as `conversations[3].chat_messages[7]`. The reader does no redaction, and injected prompt text, thinking and tool output stay in the sessions, so redact before sharing.

## Converting to STS

`pensieve-traces convert` turns a Claude Code session file or project directory, or a claude.ai export, into [STS](https://huggingface.co/docs/hub/main/en/session-traces-format) JSONL for the Hugging Face trace viewer, one `<session id>.jsonl` per session (stdlib only):

```bash
pip install "pensieve-traces[redact]"
pensieve-traces convert path/to/<project-dir> --to sts --out traces/
pensieve-traces convert path/to/claude-export --to sts --out traces/   # claude.ai export
```

- Input is detected: a directory with `conversations.json` and no `*.jsonl` (or a file named `conversations.json`) is a claude.ai export; a directory of `*.jsonl` files or one session file is Claude Code. A directory holding both is refused as ambiguous, and one holding neither is an error. `--from claude-code` or `--from claude-ai` forces the reader. Exports write one `<conversation uuid>.jsonl` per conversation with `harness: "claude.ai"` in the header; attachment text is left out. The Hub's rendering of the `claude.ai` harness id is unverified (STS only lists `llama.app`).

- Redaction is on by default. Without the `redact` extra the command exits with an error and writes nothing; pass `--no-redact` to write unredacted output (a warning is printed).
- `--out` must not be the input directory (or inside it), nor the directory of an input file (for an export, its directory): Claude Code names sessions `<session id>.jsonl`, so writing there would overwrite the originals. The command refuses and writes nothing.
- Sidechain (subagent) messages are skipped; add `--include-sidechains` to keep them.
- Fields STS has no place for are dropped: parent ids, usage, request ids, `is_meta`/`is_error` flags, metadata, `cwd`, `project`, branch and session start/end times. Meta messages are still written.
- From Python: `pensieve_traces.writers.sts.write_session(session, path)` and `session_to_sts_lines(session)` (the latter does not redact).

## Converting to ConvoMem conversations

`pensieve-traces convert --to convomem-conversations` exports a project's sessions as [ConvoMem](https://huggingface.co/datasets/Salesforce/ConvoMem)-style conversations (one session per conversation, ordered by start time), loadable by [convomem-arena](https://github.com/rahmansunny071/convomem-arena):

```bash
pensieve-traces convert path/to/<project-dir> --to convomem-conversations --out convomem/
pensieve-traces convert path/to/claude-export --to convomem-conversations --out convomem/
```

Input is detected as for STS (a claude.ai export gives one conversation per chat; `--from` overrides).

`--out` is a directory and receives two files:

- `conversations.json`: `{"evidence_items": [{"conversations": [{"id", "containsEvidence", "messages": [{"speaker", "text"}]}]}]}` (the filler-file layout; `id` is the session id, speakers are `User` / `Assistant`).
- `conversations.message_ids.json`: `{conversation id: [canonical message id, ...]}`, index-aligned with each conversation's `messages`, so Q/A generation can point evidence at exact messages.

Only user prompts and assistant prose are kept; meta, sidechain, system and tool-result messages and empty turns are dropped. Assistant tool calls become one-line `[ran Bash: git status]` summaries (from Python, `tool_activity="drop"` omits them). Redaction is on by default, as for STS, and one anonymizer covers all sessions. The command refuses (writing nothing) if either output file already exists or `--out` would overwrite the input sessions. From Python: `pensieve_traces.writers.convomem.write_conversations(sessions, out_dir)`, and `sessions_to_conversations(sessions)` / `sessions_to_conversations_with_ids(sessions)` (no redaction).

## Uploading to the Hub

`pensieve-traces upload` pushes converted traces to a Hugging Face dataset, **private by default**. Uploading is always intentional: `upload` is the only command that contacts the Hub, and `convert` and every other command only write local files. It needs the optional extra and your own login (`hf auth login`); the tool never asks for, stores or prints a token.

```bash
pip install "pensieve-traces[hub]"
pensieve-traces upload traces/ --repo <user>/<dataset>   # private unless --public
```

- `convert` writes the STS header key `pensieveRedacted` (`true` when redaction ran, `false` with `--no-redact`). `upload` refuses any file whose first line lacks `"pensieveRedacted": true` (a missing marker counts as unredacted) unless you pass `--allow-unredacted`. The marker is an attestation, not proof; review traces before sharing.
- Only the top-level `*.jsonl` files of the directory are uploaded, at the repo root.
- If any file has `"harness": "claude.ai"` in its header, `upload` prints a warning with their count before the confirmation prompt (also with `--yes`) saying redaction does not catch names, health details, money amounts or card and bank account numbers and catches only some phone numbers. It never refuses on this basis; see "Redaction" for the measured coverage.
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
| `factory:ready-to-merge` | factory skill | Every gate passed but the merge was refused: you merge it |

Agent rules live in [`CLAUDE.md`](CLAUDE.md); the stage prompts in [`.claude/commands/`](.claude/commands/).

The **`factory` skill** (`.claude/skills/factory/`) runs the same pipeline from a Claude Code session. Two of its rules:

- **Docs ship with the code:** every PR updates the docs its change affects, and its Handoff says which (or why none). Older mismatches become sub-issues of the "Documentation tech-debt" issue (#43).
- **Ready to merge:** if every gate passes but the merge is refused (the harness denies it or GitHub rejects it), the skill never works around it. It labels the PR `factory:ready-to-merge`, comments the exact merge command and carries on; `queue.py` and the end-of-run report list what waits for you. To let it merge by itself instead, add this [permission rule](https://code.claude.com/docs/en/permissions) to `.claude/settings.json` or `~/.claude/settings.json` yourself. It lets the agent use your ruleset bypass (`--admin`), so the repo doesn't ship it:

  ```json
  { "permissions": { "allow": ["Bash(gh pr merge * --merge --admin --delete-branch)"] } }
  ```

Two companion skills ship with it. **`sync-root-factory`** (`.claude/skills/sync-root-factory/`) brings the factory skill up to the latest agent-factory release as a PR ("sync the factory to the latest release"); the version this repo is on is in `.claude/skills/factory/VERSION`. **`factory-lesson`** (`.claude/skills/factory-lesson/`) files a factory problem as a `lesson` issue in agent-factory ("file a factory lesson: …"), after one confirmation and with no real data in the text.

## Development

```bash
uv sync --locked --extra dev
uvx ruff@0.17.4 format --check . && uvx ruff@0.17.4 check .
uv run --no-sync pytest -q
```

## Author

Created by **[Sajjadur Rahman](https://sajjadur.net/)** · [rahmansunny071@gmail.com](mailto:rahmansunny071@gmail.com)
