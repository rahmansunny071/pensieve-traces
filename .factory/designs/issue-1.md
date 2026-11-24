# Design: Canonical session model (Session, Message, ToolCall, Usage) (#1)

## Problem
Every reader and writer needs one harness-neutral representation of a session, so no format is ever converted directly to another. Today the package has no model. Without it, STS, OpenRouter and ConvoMem outputs cannot be derived from one lossless source.

## Proposed approach
New module `pensieve_traces/model.py` using stdlib `dataclasses` (no new dependency; the base install stays dependency-free).

- `Usage`: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` (ints, default 0).
- `ToolCall`: `id`, `name`, `arguments` (JSON **string**, as the issue specifies).
- `Message`: `id`, `role` (`Role` str-enum: user/assistant/system/tool), `parent_id`, `timestamp`, `text`, `reasoning`, `tool_calls: list[ToolCall]`, `tool_call_id`, `model`, `request_id`, `usage`, `is_sidechain`, `is_meta`.
- `Session`: `id`, `harness`, `project`, `cwd`, `title`, `started_at`, `ended_at`, `git_branch`, `metadata: dict[str, Any]`, `messages: list[Message]` (order preserved).
- Timestamps are timezone-aware `datetime`, serialised as ISO 8601; a naive datetime raises `ValueError` in `__post_init__`.
- Validation in `__post_init__`: role is a valid `Role`; unique message ids; `tool_call_id` is only set on `tool` messages.
- Serialisation: `to_dict()` / `from_dict()` on each class, plus `Session.to_json()` / `Session.from_json()`. These are the dataclass equivalents of `model_dump_json` / `model_validate_json`. Round-trips are lossless.
- Helpers: `Session.message(id)`, `parent_of(msg)`, `children_of(msg)`, `tool_result_for(call_id)`, `tool_calls()`, and `tool_call(call_id)` (the call a result answers).
- Id lookups use a lazily built private index, which is excluded from equality and serialisation.
- Export the four classes plus `Role` from `pensieve_traces`.

## Alternatives rejected
- Pydantic: nicer validation, but a new base dependency that the design does not list and CLAUDE.md restricts.
- `arguments` as a parsed dict: lossy for malformed or non-object JSON that real traces contain, and the issue asks for a string.

## Files & interfaces touched
- `pensieve_traces/model.py` — NEW
- `pensieve_traces/__init__.py` — CHANGED (re-exports)
- `tests/test_model.py` — NEW
- `README.md` — CHANGED (short "Canonical model" section)
- `CHANGELOG.md` — CHANGED

## Test plan
`tests/test_model.py` builds one small hand-written session in code (no fixture files, no real data). It has a user turn, an assistant turn with reasoning, usage and two tool calls, tool-result messages, a sidechain subagent message and an `is_meta` message. It covers:
- every field is set and survives a JSON round-trip with equality;
- message order is preserved;
- `tool_result_for`, `parent_of` and `children_of` resolve correctly, and unknown ids return `None`;
- validation errors (duplicate ids, naive datetime, bad role).

All tests run offline.

## Risks
- Privacy: the model holds raw text and tool output. It does no redaction, which is out of scope. Redaction must run before any writer, and later issues must default it to ON. Tests use only synthetic strings.
- Spec conformance: the STS and Hub viewer mappings are not checked here. The fields must be sufficient for them, so a later writer may need additive fields.
- Dependencies: none added.
- The issue text contained no instructions aimed at the agent.

## Open questions
- Dataclasses or Pydantic? I recommend dataclasses to keep the base install dependency-free. Pydantic would need approval and, per CLAUDE.md, probably an extra.
- Should `metadata` on `Message` also exist for harness-specific leftovers? I propose no for now, and adding it later is additive.

## Size estimate
S
