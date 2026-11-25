# Design: Chat PII redaction coverage and claude.ai upload warning (#48)

## Problem
Redaction (teich `TraceAnonymizer`) was tuned on coding sessions; claude.ai chats hold addresses, phones, health and money details. Nobody has measured what is caught, and `upload` says nothing when claude.ai traces are about to go to the Hub.

## Proposed approach
1. Measure (done locally, teich 0.3.x, on synthetic text): email `@vexlor-mail.io` replaced; government-ID-style number (SSN pattern) replaced; street address, phone numbers (several formats), person name, health detail, money amount, IBAN NOT replaced. `@example.com` is not replaced either (teich allow-lists it), so the fixture email uses a fictional `vexlor-mail.io` domain, like existing fixtures.
2. NEW `tests/fixtures/redact/chat_pii_session.json`: canonical `Session` (harness from the claude.ai reader) with one message per kind, obviously fictional (42 Maple Lane, 555-01xx, "Jane Doe").
3. NEW `tests/test_redact_chat_coverage.py`: `pytest.importorskip("teich.anonymize")`, runs `redact_session`, asserts a pinned `EXPECTED_COVERAGE: dict[str, bool]` (kind -> replaced). A teich upgrade that changes coverage fails with a diff of that table.
4. NEW `upload.count_harness(files, harness) -> int` reads only the first line, decodes the STS header, counts `header.get("harness") == harness`; odd files count as not matching. CLI imports `HARNESS` from `readers.claude_ai`.
5. CHANGED `cli._upload`: after the summary, before the `--yes`/`_confirm()` branch, if count > 0 call `_warn(...)` (stderr). Never refuses.

Warning text: `{n} file(s) are claude.ai chats (harness "claude.ai"); redaction does not catch addresses, phone numbers, names, health or financial details. Review them before uploading.` (printed via `_warn`, so prefixed `pensieve-traces: warning: `; "file"/"files" pluralised.)

Pinned table (measured):
| kind | replaced |
|---|---|
| email | yes |
| gov_id (SSN-style) | yes |
| street_address | no |
| phone | no |
| person_name | no |
| health_detail | no |
| money_amount | no |
| iban | no |

## Alternatives rejected
- Refuse upload of claude.ai files: the issue says warn, never refuse.
- Detect claude.ai by filename or by reading whole files: the header `harness` is authoritative and one line is cheap.

## Files & interfaces touched
- NEW tests/fixtures/redact/chat_pii_session.json
- NEW tests/test_redact_chat_coverage.py
- CHANGED pensieve_traces/upload.py (`count_harness`, `__all__`)
- CHANGED pensieve_traces/cli.py (`_upload`)
- CHANGED tests/test_upload.py (warning tests)
- CHANGED README.md ("Redaction" coverage list, "Uploading to the Hub" warning bullet), CHANGELOG.md, redact.py docstring line on chat coverage

## Test plan
Coverage test as above (skipped without `redact` extra; runs in CI job "Tests (redact)"). Upload tests with synthetic STS files in `tmp_path` and the faked Hub client: warning with count 2 and `--yes`; warning appears before the prompt; no warning when none are claude.ai; non-claude.ai/malformed files not counted; upload still proceeds. Offline.

## Risks
Privacy: warning only, defaults unchanged; fixture is fictional. Coverage is pinned to teich >=0.3.8,<0.4 and may differ across 0.3.x (the CI redact job will show). No new dependencies. STS output unchanged.

## Open questions
None. (Gaps shown by measurement: file follow-ups for an LLM PII pass; out of scope here.)

## Size estimate
S
