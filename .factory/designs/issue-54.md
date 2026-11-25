# Design: ConvoMem builder 2/3, evidence items for six categories (#54)

## Problem
`extract_candidates` (#8) yields candidate facts. Nothing turns them into ConvoMem evidence items (question, answer, evidence messages) for the six categories, with the quality checks and model verification the dataset needs. #55 writes the files and needs items plus drop counts.

## Proposed approach
New `pensieve_traces/builders/convomem/items.py`: `build_items(candidates, sessions, client, *, verify=True, cache_dir=None, group_chunk=50) -> tuple[list[EvidenceItem], Stats]`. `sessions` are already redacted; conversations come from `sessions_to_conversations_with_ids`.

- `CATEGORIES: dict[str, tuple[str, tuple[int, ...]]]` copies convomem-arena `data.py` (folder, counts).
- `EvidenceMessage(conversation_id, message_id, speaker, text)`; `EvidenceItem(category, evidence_count, question, answer, evidence, conversation_ids)`. `evidence_count` = distinct evidence messages, so it serves #55's `<N>_evidence` folders.
- **Grouping.** `user_facts`/`assistant_facts`/`preferences`/`implicit_connections`/`abstention`: the model proposes groups of candidate indices from the statements (prompt `items_group.txt`, chunks of `group_chunk` statements, so no cross-chunk groups). Pools: `user_fact`, `assistant_fact`, `preference`, `user_fact`+`preference`, and all but `changing_value`. Code rejects a group with an unknown index, a wrong kind, or an evidence count outside the category range (`dropped_bad_group`). `changing_facts` is code only: `changing_value` candidates by normalized `subject`, sorted by first-message timestamp (any `None` timestamp in a group drops it, `dropped_chronology`), adjacent equal values merged, at most the latest 6, at least 2.
- **Wording.** One call per group with the category prompt; reply is a one-element JSON list `[{"question","answer"}]`, parsed by #8's `_parse_reply` (imported, not copied). The model never writes evidence: code builds it from the candidates' verbatim texts. Preferences and implicit connections answers are rubrics.
- **Checks, in order, each a `Stats` counter:** `dropped_evidence_not_verbatim` (each text equals the full message at its message id); `dropped_chronology` (changing evidence not non-decreasing in time); `dropped_answer_mismatch` (changing answer lacks the last value, normalized containment); `dropped_duplicate_question` (normalized: casefold, punctuation to spaces, collapsed; across all categories, first wins); `dropped_answer_in_filler` (fact categories: terms are numbers plus capitalized non-sentence-initial words not in the question; at least 2; some message of a non-evidence conversation holds at least 80% of them; hits that are all numbers are ignored); `dropped_topic_in_filler` (abstention: question content words, at least 3, same 80% single-message rule); then `dropped_verification`. Preferences and implicit connections have no filler check, as in convomem-arena `check_filler`. Filler is every conversation of `sessions` outside the item's `conversation_ids`.
- **Verification** (`verify=True`): one call answers the question from the item's evidence conversations only (`verify_answer.txt`), one judge call (`judge_*.txt`, copied from `AnsweringEvaluation.scala`: `judge_default` for assistant/changing, `judge_user_facts_single`/`_multi` by count, `judge_rubric`, `judge_abstention`). Reply must start with RIGHT or WRONG (else WRONG). A failed model call drops the item (`dropped_model_error`).
- **Cache.** Reuse #8's `_cache_path/_cache_read/_cache_write` (same rules, keyed on exact request, model, format version) for every call; free-text replies are stored as `[text]`. A crash rerun re-pays nothing; a prompt edit changes the key.
- `Stats`: `model_calls`, `cache_hits`, `groups_proposed`, `dropped_bad_json`, the drops above, `items`, `items_by_category`; `to_dict()`.
- `item_prompt_hashes()` for #55's manifest. Prompt files (Apache-2.0 header, `=====PROMPT=====`): `items_group.txt`, `items_{user_facts,assistant_facts,changing_facts,abstention,preferences,implicit_connections}.txt`, `verify_answer.txt`, `judge_*.txt` (5).

## Alternatives rejected
- Model writes the evidence text: breaks the verbatim guarantee.
- Import convomem-arena's checks/judges: not a dependency.

## Files & interfaces touched
- NEW `pensieve_traces/builders/convomem/items.py`, `_common.py`, `_text.py`, 14 prompt files, `tests/test_convomem_items.py`.
- CHANGED `builders/convomem/extract.py` (imports shared helpers, no behaviour change), `builders/convomem/__init__.py` (exports, docstring), `NOTICE` (prompts now also write questions and judge answers), `CHANGELOG.md`, this design's implementation notes.

## Test plan
Scripted fake `ChatClient`, synthetic sessions: one test per check with a hand-built failing case; out-of-order changing candidates sorted; a mixed `claude_code` + `claude.ai` item; `verify=False` vs a WRONG judge dropping the item; abstention passes on "I don't know"; range limits per category; cache rerun makes no calls; prompt files load with headers.

## Risks
Privacy: text goes to the client; sessions must be pre-redacted (docstring, as #8). Long evidence conversations may overflow a small local context (no truncation). Filler term check is a simplified port of convomem-arena's `specifics`. No new dependencies.

## Orchestrator changes (approved)
- Judge prompts are copied verbatim from ConvoMem `AnsweringEvaluation.scala` (header added, template syntax adapted); wording prompts adapted from the per-category evidence generators.
- The filler check ports convomem-arena's `check_filler` faithfully, with `specifics` and the `text.py` helpers it uses (same tokenization, thresholds, "numbers alone don't count") into NEW `builders/convomem/_text.py`; tests rewrite some of its own cases as synthetic data. NOTICE gets an attribution line (convomem-arena has no license file; same author as this repo).
- Abstention answers are model-written; the abstention judge decides pass/fail.
- Shared helpers (`_parse_reply`, cache functions) move to NEW `builders/convomem/_common.py`, imported by `extract.py` and `items.py`. Extraction's public API, cache keys and `CACHE_FORMAT_VERSION` stay unchanged.
- Docstring states groups never span `group_chunk` boundaries; `item_prompt_hashes()` keys mirror `prompt_hashes()`.

## Open questions
None.

## Size estimate
M
