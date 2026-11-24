# Design: STS writer + `pensieve-traces convert --to sts` CLI (#4)

## Problem
Users can read Claude Code sessions and redact them, but cannot produce a file the Hugging Face trace viewer accepts, and there is no CLI. They need one command that turns a session file or project directory into redacted STS JSONL, one file per session.

## Proposed approach
Spec (confirmed): https://huggingface.co/docs/hub/main/en/session-traces-format.
- NEW `pensieve_traces/writers/sts.py`:
  - `session_to_sts_lines(session, *, include_sidechains=False) -> list[dict]`: pure mapper, no redaction.
  - `write_session(session, path, *, redact=True, include_sidechains=False, anonymizer=None) -> None`: calls `prepare_for_output(session, redact=redact, anonymizer=anonymizer)` first, so the gate cannot be bypassed, then writes one `json.dumps(line, ensure_ascii=False)` per line, UTF-8, `\n`.
- Header: `{"type":"session","harness":session.harness or "unknown","id":session.id,"name":session.title}` (`name` omitted if None; no extra header keys).
- Message line `{"type":"message","message":{...}}`: `role` (value), `content`=`text`, `reasoningContent`=`reasoning` (omitted if None), `toolCalls`=`[{"id","function":{"name","arguments"}}]` (omitted if empty; empty `arguments` becomes `"{}"`, otherwise kept verbatim as a string), `toolCallId` (tool messages only), `timestamp`=epoch ms int (omitted if None), `model` (omitted if None). Only those keys.
- Not mapped, dropped: `parent_id` (STS is flat; model order kept), `usage`, `request_id`, `is_meta`, `is_error`, `is_sidechain`, message and session `metadata`, `cwd`, `project`, `git_branch`, `started_at`, `ended_at`. Nothing is moved into extra fields.
- Sidechain messages are skipped unless `include_sidechains=True`.
- NEW `pensieve_traces/cli.py`: stdlib `argparse`, `main(argv=None) -> int`. `pensieve-traces convert INPUT --to {sts} --out DIR [--no-redact] [--include-sidechains]`. `INPUT` file uses `read_session`; directory uses `read_project`. Output is `DIR/<session.id>.jsonl` (DIR is created; the id must match `[A-Za-z0-9._-]+` and not start with `.`, else exit 2 for that run before writing). Existing files are overwritten. A module-level `WRITERS: dict[str, Callable]` maps `--to` choices to writers, so #6 and later formats add one entry.
- Redaction: on by default. If teich is missing, `write_session` raises `RedactionUnavailableError`; `convert` prints its install hint plus "or pass --no-redact to write unredacted output" to stderr, exits 2 and writes nothing (the first `write_session` call raises before opening its file, so nothing is written; the CLI only calls `write_session(..., redact=not args.no_redact)` and never redacts up front). `--no-redact` passes `redact=False`, which emits redact.py's `UserWarning`, and `convert` prints it to stderr. Redaction is never skipped silently.
- pyproject.toml: add `[project.scripts] pensieve-traces = "pensieve_traces.cli:main"`. No new dependency; `dependencies` stays `[]`.

## Alternatives rejected
- click/typer: new dependency for one command.
- Redacting inside `session_to_sts_lines`: mixes mapping with policy; the gate belongs in `write_session`.

## Files & interfaces touched
- NEW `pensieve_traces/writers/__init__.py`, `pensieve_traces/writers/sts.py`, `pensieve_traces/cli.py`
- NEW `tests/test_sts_writer.py`, `tests/test_cli.py`, `tests/fixtures/sts/*.jsonl` (goldens)
- CHANGED `pyproject.toml`, `README.md` (usage), `CHANGELOG.md`

## Test plan
Offline, synthetic only; reuse `tests/fixtures/claude_code/` (plain_chat, thinking, sidechain, tool results).
- Golden: reader output on fixtures, mapped with a stub anonymizer or `redact=False`, compared line by line to `tests/fixtures/sts/*.jsonl`.
- Unit: key sets exactly as above, camelCase, `arguments` is a str, timestamp is an int in ms, sidechain excluded and included, header first, empty arguments.
- CLI via `main([...])`: one file per session, directory input, default redaction with injected fake `teich.anonymize` in `sys.modules`, missing teich gives exit 2, a stderr hint and no files, `--no-redact` warns, bad session id rejected.
- Real teich test with `pytest.importorskip("teich")`.

## Risks
- Privacy: `--no-redact` is the only unredacted path and warns; redaction is best-effort, so review before sharing. Fixtures have no real data.
- Spec: no unknown keys on message objects; header has none extra. The viewer was not run.
- `is_error` is lost, so failed tool results look like normal ones.
- Dangling parents (#17) do not matter, since parents are not written.

## Open questions
- Does the Hub viewer render `harness: "claude_code"` in STS? The spec lists only `llama.app` as recognized, so the label or icon may be generic. Using the reader's value.
- Should failed tool results (`is_error`) be flagged in `content` text? Not specified; dropped for now.

## Size estimate
M
