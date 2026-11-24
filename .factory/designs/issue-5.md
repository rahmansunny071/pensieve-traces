# Design: ConvoMem conversations export (#5)

## Problem
A Claude Code project cannot yet be used as a persona's history or filler in ConvoMem-style benchmarks. Users need `convert --to convomem-conversations` to emit conversations in the shape convomem-arena reads, plus a side map from each conversation message back to its canonical message id (the contract #8 builds on).

## Proposed approach
NEW `pensieve_traces/writers/convomem.py`, going through the canonical `Session` only:
- `sessions_to_conversations(sessions, tool_activity="summarize") -> list[dict]` (the issue's contract) returns `[0]` of the sibling `sessions_to_conversations_with_ids(sessions, tool_activity="summarize") -> tuple[list[dict], dict[str, list[str]]]` (one code path). Pure mappers, no redaction. Sessions are sorted inside the mapper by `(started_at is None, started_at, id)`. Each conversation is `{"id": session.id, "containsEvidence": False, "messages": [{"speaker", "text"}]}`. Kept: user -> `User`, assistant -> `Assistant`; system and tool-result messages dropped (tool results are dropped in both modes); `is_meta` and `is_sidechain` excluded; empty or whitespace-only text dropped. Consecutive same-speaker messages are not merged. Conversations with no messages are omitted. `"drop"` uses `msg.text` only; `"summarize"` appends one line per tool call, `[ran NAME: ARG]`, ARG being the JSON `command` argument if a string (built from already-redacted arguments, truncated to 80 chars after redaction), else `[ran NAME]`. Bad value: `ValueError`.
- ID map: `{conversation_id: [canonical message id, ...]}`, 1:1 and in order with that conversation's `messages`.
- `write_conversations(sessions, out_dir, *, tool_activity="summarize", redact=True, anonymizer: Anonymizer | None = None) -> tuple[Path, Path]`: every session goes through `prepare_for_output(session, redact=redact, anonymizer=anonymizer)` (no new redaction logic). One anonymizer is shared across all sessions, so placeholders are consistent across conversations (built once when `anonymizer is None` and `redact`). It calls the sibling mapper, and only after everything is built and redacted does it check targets and open files. It writes in `out_dir`:
  - `conversations.json`: the filler wrapper `{"evidence_items": [{"conversations": [...]}]}`, which `parse_filler_file` loads directly; each conversation satisfies `parse_conversation`.
  - `conversations.message_ids.json`: the ID map.
  If either file already exists it raises, writing nothing. No redaction marker (`upload` is STS-only).
- CLI: choice `convomem-conversations` added to `--to`; `--out` is a DIRECTORY, as for STS. `_convert` branches before the per-session loop (one aggregate output), runs `_check_out_dir` (inputs are `*.jsonl`, outputs `*.json`), keeps the warnings capture and the `RedactionUnavailableError` -> `CliError` hint, refuses with `CliError` if either output file exists, and rejects `--include-sidechains` for this format. Prints both paths.
- No new dependencies; version unchanged.

## Alternatives rejected
- `--out` as a file (the issue's `conv.json`): breaks the directory contract and the `_check_out_dir` guard; the ID map would need a derived name.
- Bare list at top level: nothing in convomem-arena loads it; only the wrapper has a loader.
- Overwrite or a `--force` flag: outputs could destroy user files; refuse instead.

## Files & interfaces touched
- NEW `pensieve_traces/writers/convomem.py`; NEW `tests/test_convomem_writer.py`; NEW `tests/fixtures/convomem/` (synthetic session, golden `conversations.json` and `conversations.message_ids.json`)
- CHANGED `pensieve_traces/cli.py`, `tests/test_cli.py`, `README.md` (status `planned` -> available), `CHANGELOG.md`

## Test plan
Offline; an injected fake anonymizer (runs without teich) plus one teich-backed test that skips without teich. Cases: both `tool_activity` modes against goldens; meta, sidechain, system, tool-result and empty turns excluded; start-time ordering with a None `started_at`; whitespace-only text dropped; unique conversation ids; one anonymizer shared across sessions; exact speakers; ID map aligned with messages; output parsed with an in-test copy of `parse_conversation`/`parse_filler_file` logic; redaction on by default (secret absent, redacted marker present) and `redact=False`; CLI: files written, existing output refused with nothing written, `--out` equal to the input dir refused, sidechain flag rejected, missing teich gives the existing `--no-redact` hint.

## Risks
- Privacy: tool summaries carry command text; redaction runs first on arguments and text, and is on by default. Fixtures synthetic.
- Spec: convomem-arena shape read from `data.py`; the HF dataset page was not fetched, so `containsEvidence` is taken from the issue.
- No new or heavy dependencies.

## Open questions
None.

## Size estimate
M

## Implementation notes
- Built: `pensieve_traces/writers/convomem.py` (`sessions_to_conversations`, `sessions_to_conversations_with_ids`, `write_conversations`) and `convert --to convomem-conversations --out DIR` (PR #40).
- Contract for #8: `DIR/conversations.json` is the filler wrapper `{"evidence_items": [{"conversations": [{id, containsEvidence: false, messages[{speaker, text}]}]}]}`; `DIR/conversations.message_ids.json` maps conversation id → canonical message ids, 1:1 and in order with `messages`. Conversation id = session id.
- Kept: User/Assistant turns only; dropped meta, sidechain, system, tool results and empty/whitespace-only turns; same-speaker turns not merged. `summarize` appends `[ran NAME: ARG]` (Bash-style `command` only, from redacted arguments, ARG ≤ 80 chars).
- Safety: redaction on by default via `prepare_for_output`, one anonymizer shared across all sessions in a write (consistent placeholders); `_check_out_dir` guard; refuses and writes nothing if either output exists; `--include-sidechains` rejected.
- Design review (REVISE, 2 changes) decided the four open questions without escalation: wrapper, `--out` directory, refuse existing outputs, drop tool results.
- Follow-ups: #41 (duplicate session ids, all-or-nothing pair, cross-session placeholder test, optional `--force`).
