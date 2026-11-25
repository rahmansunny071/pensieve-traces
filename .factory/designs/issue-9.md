# Design: check-convomem structure check for generated datasets (#9)

## Problem
After `build convomem` there is no way to know the folder is valid ConvoMem before running memory systems on it. Hand edits or a bug could break the layout silently. Users need a dependency-free structure check, plus documented steps for convomem-arena's full data check.

## Proposed approach
New `builders/convomem/check.py`: `check_dataset(path) -> list[Problem]` and `summarize(path) -> dict[str, tuple[int, int]]` (category to files, items). `Problem` is a frozen dataclass `(file: str, item: int | None, reason: str)`; `file` is relative to DIR. Reads only DIR, writes nothing, never raises on bad content (a bad file becomes a Problem). `CATEGORIES` is imported from `items.py`; `EVIDENCE_ROOT`/`FILLER_ROOT` from `dataset.py`.

Rules:
1. DIR is a directory with `core_benchmark/evidence_questions/`.
2. Each child is a known category folder (else "unknown category folder"); each grandchild is `<N>_evidence`, N an int in the category's range (else "evidence count N not allowed").
3. Each `*.json` parses to `{"evidence_items": [...]}`.
4. Each item: non-empty `question` and `answer` strings, non-empty `conversations`, `message_evidences` a list of length N (abstention: 1-3, as the issue says).
4b. (added by the orchestrator) `len(message_evidences)` equals the number of the item's conversations with `containsEvidence: true`, and each evidence text is found in a different one of them (convomem-arena's `check_structure` flags this as error, and ConvoMem's `ConversationValidator` rejects it).
5. Each `message_evidences[].text` equals, verbatim, some message `text` in the item's conversations.
6. `changing_evidence`: evidence texts occur in conversation order (conversation index strictly increasing).
7. A same-named file exists in `core_benchmark/filler_conversations/` (else problem), has the filler layout, and holds no conversation id used as evidence by any item of any file with that name.

CLI: `pensieve-traces check-convomem DIR`. stdout: one line per category `<category folder>: F files, I items`, then one line per problem `file[item i]: reason`, then `N problems` / `ok`. Exit 1 on any problem, 0 otherwise. A missing DIR is a `CliError` (exit 1, stderr).

## Alternatives rejected
- Depending on convomem-arena: not hardened for PyPI, no git-URL deps (maintainer decision).
- Re-implementing content checks (grounding, duplicates, filler contamination by words): stays in convomem-arena.

## Files & interfaces touched
- NEW `pensieve_traces/builders/convomem/check.py`; CHANGED `builders/convomem/__init__.py` (export `check_dataset`, `Problem`, docstring), `cli.py` (subcommand `check-convomem`)
- NEW `tests/test_convomem_check.py`; CHANGED `tests/test_cli_build.py` only if reusing helpers
- CHANGED `README.md` ("Checking a ConvoMem dataset" under "Building a ConvoMem dataset", not "Redaction"; Python sentence), `CHANGELOG.md`, this design's implementation notes

## Test plan
Offline. A test helper builds the dataset with `dataset.write_dataset` (as #55 writes it, one `me.json`, a few categories, extra `topic`/`topics` keys) from hand-written synthetic items; no committed real data. The issue says "in `tests/fixtures/`": the generated dataset is built under `tmp_path` rather than committed, which stays in sync with the writer. The clean dataset passes with expected counts; each variant (edit the JSON in `tmp_path`) is reported: missing evidence text, count outside range, out-of-order changing evidence, evidence conversation in filler, unknown category folder, plus missing filler file, bad JSON, and DIR unchanged after the check. CLI: exit codes and output.

## Risks
Privacy: reads and prints only paths, indices and reasons, never message text. No redaction applies (no output of content). No new dependency; no network, no Hub contact.

## Open questions
None. Orchestrator answers: (1) rule 4b added, with a broken variant in the tests; (2) the convomem-arena README steps are worded "once convomem-arena#18 lands", linking it, with no config field named; (3) the clean dataset is generated in `tmp_path` with `dataset.write_dataset` (see Deviations).

## Deviations from the issue
The issue says the synthetic dataset lives in `tests/fixtures/`; it is generated under `tmp_path` with `dataset.write_dataset` so it stays in sync with the writer.

## Size estimate
S/M

## Implementation notes
- Built as planned in `builders/convomem/check.py` (`Problem`, `check_dataset`, `summarize`) and `cli._check_convomem`. Exit 1 on problems is a `SystemExit(1)` after printing; a missing DIR is a `CliError`.
- Distinctness (rule 4b) is a bipartite matching of evidence texts to the conversations marked `containsEvidence`; changing-evidence order is checked on those conversations, only when all texts are found.
- The filler check is keyed on the file name: evidence ids of all evidence files named `X.json` must not appear in `filler_conversations/X.json`.
- Tests generate the clean dataset in `tmp_path` with `dataset.write_dataset` (with topics keys), then edit JSON for each broken variant.
