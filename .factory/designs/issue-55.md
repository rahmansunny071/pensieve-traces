# Design: `build convomem` over Claude Code sessions and claude.ai exports (#55)

## Problem
Extraction (#8) and item building (#54) exist as library calls only. A user cannot turn their Claude Code projects and claude.ai exports into a ConvoMem dataset in the layout convomem-arena reads. The text goes to a model endpoint and the result is a dataset of personal facts, so redaction, the endpoint, and what gets overwritten must be safe by default.

## Proposed approach
`pensieve-traces build convomem INPUT... --out DIR --model M`, flags: `--from {claude-code,claude-ai}` (as `convert`, applies to every input), `--base-url` (default `http://localhost:11434/v1`), `--api-key-env NAME`, `--allow-remote-model`, `--topics FILE`, `--cache-dir DIR`, `--no-redact`.

Order in `cli._build_convomem(args)`; nothing is written before step 7, and step 7 writes only the cache dir:
1. **Endpoint guard**, before reading inputs: hostname must be exactly `localhost`, `127.0.0.1` or `::1`, scheme http/https, no userinfo (always refused); otherwise error unless `--allow-remote-model`. Print `model endpoint: host:port` (no path, credentials or key) before step 4.
2. `load_topics` (new `builders/convomem/topics.py`), so a bad file fails before any model call.
3. Path guards (`_check_dataset_paths`): `--out` equal to or inside any input (directory, or the parent directory of a file input), or already containing `core_benchmark/` or `build_manifest.json`; cache dir (default `<out>.cache`, a sibling) equal to `--out`, inside `--out`, or inside an input. Same resolve and `is_relative_to` rules as `_check_out_dir`.
4. `load_sessions` per input; dedupe by session id across inputs (first input wins; count kept for the manifest, #59). Zero sessions: error.
5. Redaction (default on): one `_default_anonymizer()`, then `prepare_for_output` over every session once. `RedactionUnavailableError` is a `CliError` (`--no-redact` hint), nothing written; `--no-redact` prints the warning. Topic matching uses the original sessions' ids, projects and titles, locally; matcher text is never sent.
6. `client = make_client(model, base_url, api_key_env)`: the injection seam, a module-level function in `cli.py`, looked up at call time; tests `monkeypatch.setattr(cli, "make_client", ...)`.
7. `extract_candidates` then `build_items`, both with `cache_dir`. Error, nothing written, if `failed_windows + dropped_bad_json == windows` or zero items.
8. `dataset.write_dataset` into a temp dir `<out>.tmp-XXXX` (sibling, same filesystem), then move into place: if `--out` is absent, rename the temp dir; if it exists, move `core_benchmark/` and `build_manifest.json` in (both refused if present). Temp dir removed on any error. Prints the `--out` path.

**New `builders/convomem/dataset.py`**: `write_dataset(out, items, conversations, topics, manifest)`.
- `core_benchmark/evidence_questions/<CATEGORIES folder>/<evidence_count>_evidence/me.json`, `{"evidence_items": [{question, answer, message_evidences: [{speaker, text}], conversations: [{id, containsEvidence: true, messages}], topics?}]}`; empty folders not created.
- `core_benchmark/filler_conversations/me.json`: the filler-file layout of `writers/convomem.py`.
- `build_manifest.json` keys: `format_version`, `inputs` (`count`, `sessions_read`, `duplicates_dropped`, `sessions_by_harness`; no paths), `model`, `endpoint_local`, `redaction`, `prompt_hashes` (`extract`, `items`), `conversations` (`total`, `evidence`, `filler`), `items_by_category`, `extract_stats`, `item_stats`, `topics` (`enabled`, `count`). No base URL, key or timestamp.

**Filler (settles the note on #55).** convomem-arena's `mix()` excludes an item's own evidence conversations, but `data_check.check_filler` runs against the whole persona filler file with no exclusion, so a filler file holding every conversation makes most fact and abstention items flag `answer_terms_in_filler`/`question_topic_in_filler` in #9. So the filler file holds only conversations that are evidence for no item. #54's own `_in_filler` check already passes each item against all other conversations, a superset of this set, so the arena check cannot newly flag it. Cost: filler shrinks by the number of distinct evidence conversations; if every conversation is evidence, the file has an empty list and `mix()` yields evidence only. Counts are in the manifest. This deviates from the issue text ("every conversation").

**Topics file**: an object, topic name to `{"ids": [...], "projects": [...], "titles": [...]}`; all keys optional lists of non-empty strings, at least one non-empty matcher per topic, no unknown keys, no duplicate JSON keys. `TopicsError` messages: `not valid JSON`, `must be an object`, `topic name must be non-empty`, `topic 'X': unknown key 'k'`, `topic 'X': 'k' must be a list of non-empty strings`, `topic 'X': no matchers`. Match: `ids` exact, `projects` exact casefold, `titles` casefold substring. With `--topics`, every conversation (evidence and filler) gets `"topic": [...]` and every item `"topics"` (union over its evidence conversations), possibly empty; without it neither key.

## Design review changes (approved, review 6059441537)
- **Move (step 8):** `core_benchmark/` and `build_manifest.json` are re-checked immediately before the move, and only no-clobber operations are used: with an existing `--out`, `core_benchmark/` is renamed in first, then the manifest is written to `--out` with mode "x"; if the second step fails, `core_benchmark/` is moved back into the temp dir, and if that rollback fails the temp dir is kept and its path printed. The temp dir is removed on any exception, Ctrl-C included.
- **Planted email:** planted in both inputs; a fake anonymizer counts its instances (exactly 1, "once over all inputs"); email absent (case-insensitive) from every client message and every file under `--out` and `<out>.cache`; an `importorskip("teich")` variant; missing teich: `make_client` never called, no `--out`, `<out>.cache` or `<out>.tmp-*`.
- **Guidance applied:** file inputs refuse only `out == parent` (as `_check_out_dir`); `--out` that is an existing file refused; default cache path built from the resolved `--out`; bad port or missing hostname is a `CliError`; an unset `--api-key-env` variable fails early naming only the variable; redaction in load order (changing inputs can miss part of a warm cache; documented); `--no-redact` warning printed once, endpoint line does not say "redacted" then; zero windows has its own error; README points at the Redaction table and defines `projects` as the basename of the session's working directory (claude.ai sessions have none); `cli.py` docstring and README output-table row updated; the full-run test asserts the arena structure invariants.
- **Settled:** the filler file excludes every evidence conversation (verified against convomem-arena); #48 merged and touched only `_upload`, README "Redaction"/"Uploading", CHANGELOG.

## Alternatives rejected
- One persona file per item, each with its own filler: duplicates the corpus per item and drops the single `me.json` persona.
- Filler with every conversation: the #9 flags above.

## Files & interfaces touched
- NEW `builders/convomem/dataset.py`, `builders/convomem/topics.py`, `tests/test_convomem_dataset.py`, `tests/test_cli_build.py`.
- CHANGED `cli.py` (`build convomem`, `make_client`, path guards; `_upload` untouched), `builders/convomem/__init__.py`.
- Docs: README (new "Building a ConvoMem dataset", output-table row to available, "Redaction" note, intro and diagram wording), CHANGELOG, `builders/convomem/__init__.py` docstring, this design's implementation notes.

## Test plan
Offline; fake `ChatClient` via `make_client`; fake teich as in `test_cli.py`. Synthetic fixtures: the Claude Code project and claude.ai export already in `tests/fixtures`. Tests: full run checks every file and manifest key; duplicate session id across inputs; planted email absent from every client message and output file; missing teich writes nothing, `--no-redact` warns; non-local, userinfo and `localhost.example` URLs refused before inputs are read (unreadable input path still gives the endpoint error); `--allow-remote-model`; topics valid and each malformed case before a client call; out equal/inside input, existing `core_benchmark/`, cache inside input or out; temp dir removed after a client failure; filler excludes evidence conversations; rerun uses the cache.

## Risks
Privacy: redacted text goes to the endpoint, printed first; the dataset is about the user. Cache holds model replies, hence beside, not inside, the dataset. Redaction is best-effort. Context overflow on small local models (no truncation). No new dependencies; no Hub contact.

## Open questions
- Filler excludes evidence conversations (deviates from the issue text); confirm.
- Not verified: how #48 changes `README` and `cli.py`; I rebase and adapt after it merges.

## Size estimate
M

## Implementation notes
- Built as planned: `cli._build_convomem` (steps 1-8), `cli.make_client` (the injection seam), `builders/convomem/dataset.py` (`write_dataset`, `build_manifest`, `commit_dataset`, `CommitError`) and `topics.py` (`load_topics`, `match_topics`, `TopicsError`), tests in `tests/test_cli_build.py` and `tests/test_convomem_dataset.py`.
- Filler excludes every evidence conversation (the issue text said every conversation); see the README and the `dataset.py` docstring.
- Minor choices: the manifest holds the `sessions_by_harness` counts keyed by the readers' harness ids (`{"claude_code": n, "claude.ai": n}`); the endpoint line (`sending redacted text to the model endpoint host:port`, or `UNREDACTED text (redaction is off)` with `--no-redact`) is printed after redaction, just before `make_client` (plan step 1's "before step 4" is stale), and with the item summary goes to stderr, stdout carries only the `--out` path; the temp dir is `<out>.tmp-XXXX`, created by `mkdtemp` (mode 0700 until moved into a new `--out`).
- `write_dataset(root, items, conversations, manifest, *, topics_by_conversation=None, topic_order=None)` writes into an existing directory (the plan's signature with `out` and `topics` is stale). The tests build their own synthetic inputs in `tmp_path` (a Claude Code project and a claude.ai export) rather than using `tests/fixtures`.
- Tests use a scripted fake client and a counting fake anonymizer; the real-teich variant runs only in the redact job.
