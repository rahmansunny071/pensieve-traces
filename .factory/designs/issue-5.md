# Design: ConvoMem conversations export (#5)

## Problem
A Claude Code project cannot yet be used as a persona's history or filler in ConvoMem-style benchmarks. Users need `convert --to convomem-conversations` to emit conversations in the shape convomem-arena reads, plus a side map from each conversation message back to its canonical message id (the contract #8 builds on).

## Proposed approach
NEW `pensieve_traces/writers/convomem.py`, going through the canonical `Session` only:
- `sessions_to_conversations(sessions, tool_activity="summarize") -> tuple[list[dict], dict[str, list[str]]]`. Pure mapper, no redaction. Sessions sorted by `started_at` (None last, stable). Each conversation is `{"id": session.id, "containsEvidence": False, "messages": [{"speaker", "text"}]}`. Kept messages: role user -> `User`, assistant -> `Assistant`; system and tool-result messages dropped; `is_meta` and `is_sidechain` always excluded; messages with empty text (after the tool step) dropped. Conversations with no messages are omitted. `tool_activity="drop"` uses `msg.text` only; `"summarize"` appends one line per tool call to the assistant text, `[ran NAME: ARG]`, where ARG is the JSON `command` argument if a string, truncated to 80 chars, else `[ran NAME]`. Bad value: `ValueError`.
- ID map: second return value `{conversation_id: [canonical message id, ...]}`, index-aligned with that conversation's `messages` (same length, same order).
- `write_conversations(sessions, out_dir, *, tool_activity="summarize", redact=True) -> tuple[Path, Path]`: runs each session through `prepare_for_output(session, redact=redact)` (reuse, no new redaction logic; raises `RedactionUnavailableError` before any file opens), then writes in `out_dir`:
  - `conversations.json`: the filler wrapper `{"evidence_items": [{"conversations": [...]}]}`, which `parse_filler_file` loads directly and each conversation satisfies `parse_conversation`.
  - `conversations.message_ids.json`: the ID map.
  Neither carries a redaction marker (not uploadable by `upload`, which is STS-only).
- CLI: choice `convomem-conversations` added to `--to`; `--out` stays a DIRECTORY, as for STS. `_convert` branches before the per-session loop (the format is one aggregate, not one file per session), calls `_check_out_dir` (input sessions cannot be overwritten; they are `*.jsonl`, outputs `*.json`), and refuses with `CliError` if either output file already exists in `--out` (no overwrite, nothing written). `--include-sidechains` is rejected for this format. Prints both paths.
- No new dependencies; version unchanged.

## Alternatives rejected
- `--out` as a file (the issue's `conv.json`): breaks the directory contract and the `_check_out_dir` guard; the ID map would need a derived name.
- Bare list at top level: nothing in convomem-arena loads it; only the wrapper has a loader.

## Files & interfaces touched
- NEW `pensieve_traces/writers/convomem.py`; NEW `tests/test_convomem_writer.py`; NEW `tests/fixtures/convomem/` (synthetic session, golden `conversations.json` and `conversations.message_ids.json`)
- CHANGED `pensieve_traces/cli.py`, `tests/test_cli.py`, `README.md` (status `planned` -> available), `CHANGELOG.md`

## Test plan
Offline; fake anonymizer for redaction. Cases: both `tool_activity` modes against goldens; meta, sidechain, system, tool-result and empty turns excluded; start-time ordering with a None `started_at`; exact speakers; ID map aligned with messages; output parsed with an in-test copy of `parse_conversation`/`parse_filler_file` logic; redaction on by default (secret absent, redacted marker present) and `redact=False`; CLI: files written, existing output refused with nothing written, `--out` equal to the input dir refused, sidechain flag rejected, missing teich gives the existing `--no-redact` hint.

## Risks
- Privacy: tool summaries carry command text; redaction runs first on arguments and text, and is on by default. Fixtures synthetic.
- Spec: convomem-arena shape read from `data.py`; the HF dataset page was not fetched, so `containsEvidence` is taken from the issue.
- No new or heavy dependencies.

## Open questions
- Top-level wrapper (recommended) vs bare list.
- `--out` directory (recommended, consistent with STS) vs a file path as in the issue example.
- Existing outputs: refuse (recommended) vs overwrite vs a new `--force` flag.
- Tool results are dropped in both modes; only calls are summarized. OK?

## Size estimate
M
