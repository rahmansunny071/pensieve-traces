# Design: OpenRouter Broadcast writer (#7)

status: blocked (output schema not confirmable from official docs; see Open questions)

## Problem
Users want to turn a canonical `Session` into OpenRouter Broadcast trace files that the Hub opens as an "OpenRouter trace". The official docs describe the envelope and many field names but never publish a full example record or the shape of `input`/`output`/`observations`, so a conformant writer cannot be built without inventing fields.

## Verified schema excerpts (official docs)
- https://huggingface.co/docs/hub/main/en/agent-traces ("OpenRouter Broadcast traces"): "Each trace is saved as a separate JSON file named `{traceId}-{timestamp}.json`"; "A trace is one model request, not a whole session... Each file contains the conversation up to that request, which means the last file of a session holds the whole session. Files from the same session share the same `trace.sessionId`." Viewer shows messages, tool calls and results, model, token counts (incl. cached), cost. Images are stripped (`[image]`).
- https://openrouter.ai/docs/guides/features/broadcast/hugging-face: single-trace file shape `{ "trace": { ... }, "exported_at": "..." }`; trace-level `id`, `name`, `userId`, `sessionId`, `metadata`; observation-level `name`, `metadata`.
- https://openrouter.ai/docs/guides/features/broadcast/s3.md: batch files use `traces[]`; `trace.observations[].promptTokensDetails`; "Null or omitted values mean unavailable, while an explicit zero is a reported or observed zero."
- https://openrouter.ai/docs/guides/features/broadcast.md (field names only): `id, userId, sessionId, name, parentObservationId, timestamp, startTime, endTime, routerLatencyMs, input, output, model, modelParameters, providerName, providerSlug, promptTokens, completionTokens, totalTokens, cachedTokens, reasoningTokens, inputCost, outputCost, totalCost, level, statusCode, finishReason, metadata, tags, type`. Observations: root `GENERATION` plus `SPAN`s. Truncation markers `{"openrouter_truncated": {"omitted_items": N}}`.
- NOT published: any full example trace file; the structure of `input`/`output` (no role/content/tool_calls documented); observation nesting; `exported_at` format; how tool calls/results appear.

## Proposed approach (provisional, to be confirmed)
New `pensieve_traces/writers/openrouter.py` mirroring `writers/sts.py`: pure mapper `session_to_openrouter_traces(session, *, include_sidechains=False) -> list[dict]` (one file per assistant request, each holding the conversation so far, shared `sessionId = session.id`), and `write_session(session, out_dir, *, redact=True, ...)` that always calls `prepare_for_output`. File name `{traceId}-{timestamp}.json`. Mapping: `usage` -> `promptTokens/completionTokens/cachedTokens`; `model` -> `model`; cost, latency, `userId`, `providerName` are absent from the canonical model so are omitted/null, never invented. CLI: additive `pensieve-traces export --format openrouter` (flag name to match existing CLI; confirm when building). No new dependencies.

## Alternatives rejected
- Guess `input`/`output` as OpenAI chat messages: plausible but unverified; violates "match published spec exactly".
- Convert STS output to OpenRouter directly: breaks the canonical-model rule.

## Files & interfaces touched
- NEW pensieve_traces/writers/openrouter.py
- CHANGED pensieve_traces/cli.py (minimal, additive), README.md, CHANGELOG.md
- NEW tests/test_openrouter_writer.py, tests/fixtures/openrouter/ (golden files)

## Test plan
Golden-file test from a synthetic session (tool call + result, reasoning, usage), redaction-default test, file naming test, sidechain skip test; CLI test. Offline only.

## Risks
Spec conformance is the main risk (schema unpublished). Privacy: files carry full prompts; redaction stays default ON. No dependencies.

## Open questions
1. Provide (or point to) a real or documented example OpenRouter trace JSON file, or the OpenAPI/Langfuse-style schema for `trace.observations[]`, `input` and `output`, tool calls/results, and `exported_at` format. Without it the writer would invent fields.
2. Should one file be written per model request (as OpenRouter does) or only the final whole-session file?
3. Is Hub viewer acceptance of a minimal record (fields above only) sufficient, given no docs list required fields?

## Size estimate
M
