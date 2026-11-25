# Design: ConvoMem builder 1/3, model client and candidate facts (#8)

## Problem
Nothing yet turns conversations into ConvoMem material. Step 1 needs a way to ask a (local by default) language model for candidate memory facts, each tied to exact messages, from canonical `Session`s of any source. The result is the data contract that #54 (items) and #55 (command) build on.

## Proposed approach
NEW package `pensieve_traces/builders/`.
- `builders/llm.py`: `ChatClient` Protocol (`model: str`; `chat(messages: list[dict[str, str]]) -> str`); `ModelClientError(Exception)`; `OpenAICompatibleClient(model, base_url="http://localhost:11434/v1", api_key_env=None, timeout=120.0, opener=None)`. It POSTs `{model, messages, temperature: 0}` to `<base_url>/chat/completions` via `urllib.request` (`opener` is an injectable `urlopen`-style callable). The key is read from `os.environ[api_key_env]` at call time (unset: error naming the variable only), sent only via `Request.add_unredirected_header("Authorization", ...)` so redirects never carry it to another host, and never stored in `repr`, logs or errors. HTTP, network, timeout and JSON-shape failures raise `ModelClientError` with the status and a fixed short reason, never the body, URL query or key.
- `builders/convomem/__init__.py` exports `extract_candidates`, `Candidate`, `ExtractStats`, `prompt_hashes`, `CACHE_FORMAT_VERSION`.
- `builders/convomem/extract.py`: `extract_candidates(sessions, client, *, window=40, overlap=10, cache_dir=None, stats=None) -> list[Candidate]`. It calls `sessions_to_conversations_with_ids(sessions)` (default `tool_activity="summarize"`, so evidence text equals what the filler file will hold). Each conversation is cut into overlapping windows; the prompt shows messages as `[i] Speaker: text`. The model returns a JSON list of `{kind, statement, subject, value, indices}`. Code validates each item and drops invalid ones (bad JSON, unknown kind, `changing_value` without subject/value, empty or non-int or out-of-window indices) with a count; an exception from the client or a bad window only skips that window. Duplicates (same conversation, kind, sorted message ids) from overlapping windows are merged.
- `Candidate` dataclass: `kind: Literal["user_fact","assistant_fact","preference","changing_value"]`, `statement: str`, `conversation_id: str`, `message_ids: list[str]`, `speakers: list[str]`, `texts: list[str]` (verbatim full messages), `timestamps: list[datetime | None]`, `positions: list[int]` (conversation-global message indices; the model sees window-local ones), `subject: str | None`, `value: str | None`; `to_dict`/`from_dict`.
- `ExtractStats` dataclass of ints: `windows`, `model_calls`, `cache_hits`, `failed_windows`, `candidates`, `merged_duplicates`, `dropped_bad_json`, `dropped_unknown_kind`, `dropped_bad_index`, `dropped_missing_fields`. The caller passes `stats=ExtractStats()` to read it.
- Reply parsing, before validation: strip a leading `<think>...</think>`, strip one surrounding code fence, accept a bare JSON list or an object with exactly one list value; anything else is `dropped_bad_json`.
- Cache: one file per window, `<cache_dir>/<64-hex>.json`; key = sha256 of the exact rendered request messages + `client.model` + `CACHE_FORMAT_VERSION`. The parsed reply list is cached and re-validated on load. Rules: `mkdir(parents=True, exist_ok=True)`; `NotADirectoryError` before any model call if the path exists and is not a directory; only its own `<64-hex>.json` files are created or overwritten, nothing else is listed or deleted; writes are atomic (unique temp file in the same dir, then `os.replace`); a corrupt or wrong-shape entry is a miss and is replaced. "Nothing usable" for #55 is `failed_windows + dropped_bad_json == windows` (`dropped_bad_json` counts unparseable-reply windows). Failed windows are not cached. Public helper `prompt_hashes() -> dict[str, str]` hashes the prompt files for #55's manifest.
- Prompts (NEW, `builders/convomem/prompts/`): `extract_system.txt` and `extract_user.txt`, each with a header (Copyright (c) 2024 Salesforce, Inc.; Apache-2.0 notice; source URL; "modified by pensieve-traces"), stripped before sending at the fixed delimiter line `=====PROMPT=====` (#54 reuses this format), adapted for extraction from existing conversations. Loaded with `importlib.resources`.
- Privacy docstring: nothing here redacts; callers pass sessions that went through `prepare_for_output`, and the text is sent to the client's endpoint.
- No new dependencies. Hatch's wheel target already includes non-Python files in `pensieve_traces`; I will confirm the `.txt` files land in a built wheel and add a `NOTICE` file at repo root (picked up by hatch's default license-files).

## Alternatives rejected
- A hard-coded prompt string in Python: the issue asks for editable files, and file hashes feed the cache key.
- `response_format` JSON mode: not every local server supports it; we parse and validate instead.

## Files & interfaces touched
- NEW `pensieve_traces/builders/{__init__,llm}.py`, `pensieve_traces/builders/convomem/{__init__,extract}.py`, `pensieve_traces/builders/convomem/prompts/*.txt`
- NEW `NOTICE`; NEW `builders/convomem/prompts/LICENSE-Apache-2.0.txt` (full license text, in the wheel check); NEW `tests/test_llm_client.py`, `tests/test_convomem_extract.py`
- CHANGED `CHANGELOG.md`. Docs: public-function docstrings; README section waits for #55.
- `pyproject.toml` changes only if the wheel check shows the prompts missing.

## Test plan
Offline: scripted fake `ChatClient`, fake opener. Client: URL, model, headers, body, key from the named env var absent from `repr` and every raised error, 4xx/5xx/timeout/malformed JSON give `ModelClientError`. Extraction on synthetic sessions: all four kinds; every drop reason counted; one bad window does not abort; overlap duplicates merged; verbatim texts and ids match the id map; two harnesses; second run with `tmp_path` cache makes zero calls; prompt edit invalidates; prompts load as package resources; prompt edit (via `load_prompt`) recomputes; `window + 1` boundary; control characters in the key (stripped trailing CR/LF, embedded refused), `ValueError`/`IncompleteRead` wrapped; wheel check by hand.

## Risks
- Privacy: conversation text goes to the endpoint; default is localhost, no redaction here (documented), no real data in fixtures, key never printed.
- Long messages may exceed the model context; not handled in this step.
- No Hub or spec involvement; no dependencies.

## Open questions
- Do the Salesforce prompts' exact wording and Apache-2.0 header text suffice as attribution? I will read them at build time.

## Size estimate
M
