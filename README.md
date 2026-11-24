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

## Canonical model

`pensieve_traces.model` defines the harness-neutral dataclasses `Session`, `Message`, `ToolCall`, `Usage` and the `Role` enum (no extra dependencies). Timestamps are timezone-aware, tool arguments stay a raw JSON string, and `Session.to_json()` / `Session.from_json()` round-trip losslessly. The model holds raw text and does no redaction.

```python
from pensieve_traces import Session

session = Session.from_json(path.read_text())
session.tool_result_for(call_id)
```

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
