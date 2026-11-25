# Design: Chat PII redaction coverage and claude.ai upload warning (#48)

## Problem
Redaction (teich `TraceAnonymizer`) was tuned on coding sessions; claude.ai chats hold addresses, phones, health and money details. Nobody has measured what is caught, and `upload` says nothing when claude.ai traces are about to go to the Hub.

## Proposed approach
1. Measured with teich 0.3.8 (the `uv.lock` pin) on synthetic text; table below.
2. NEW `tests/fixtures/redact/chat_pii_session.json`: canonical `Session` (harness `claude.ai`), one fictional value per message, message id = kind.
3. NEW `tests/test_redact_chat_coverage.py`: `importorskip("teich.anonymize")`; a kind counts as replaced when its exact value is gone from that message's redacted text; asserts the fixture contains every value and the whole dict equals `EXPECTED_COVERAGE`.
4. NEW `upload.count_harness(files, harness) -> int`: first line only, exact `harness` match (`Claude.ai`, missing, empty, non-JSON, `[1]`, non-UTF-8 count 0, never raise). Shared `_read_header` helper also used by the redaction-marker check.
5. CHANGED `cli._upload`: after the summary, before the `--yes`/`_confirm()` branch, `_warn` if count > 0 (`HARNESS` imported from `readers.claude_ai`). Never refuses.

Warning text (`n` files, singular "1 file is"): `{n} files are claude.ai chats (harness "claude.ai"); redaction does not catch names, health details, money amounts or card and bank account numbers, and catches only some phone numbers. Review them before uploading.`

Pinned table (teich 0.3.8, measured):
| kind (value) | replaced |
|---|---|
| email (`jane.doe@vexlor-mail.invalid`) | yes |
| ssn (`987-65-4320`) | yes |
| phone_call_me_at_10_digit (`(555) 555-0142`) | yes |
| street_address (`42 Maple Lane`) | yes |
| phone_my_phone_is_7_digit (`555-0143`) | no |
| iban (`GB82WEST12345698765432`) | no |
| card_number (`4111 1111 1111 1111`) | no |
| person_name (`Jane Doe`) | no |
| health_detail (`type 2 diabetes`) | no |
| money_amount (`$4,200`) | no |

Note: the address match swallows the rest of its sentence ("42 Maple Lane, Springfield." becomes one placeholder), so coverage is trigger-phrase based.

## Alternatives rejected
- Refuse upload of claude.ai files: the issue says warn, never refuse.
- Detect claude.ai by filename or by reading whole files: the header `harness` is authoritative and one line is cheap.

## Files & interfaces touched
- NEW tests/fixtures/redact/chat_pii_session.json
- NEW tests/test_redact_chat_coverage.py
- CHANGED pensieve_traces/upload.py (`count_harness`, `__all__`)
- CHANGED pensieve_traces/cli.py (`_upload`)
- CHANGED tests/test_upload.py (warning tests)
- CHANGED README.md ("Redaction" coverage table naming teich 0.3.8, "Uploading to the Hub" warning bullet), CHANGELOG.md, redact.py docstring line on chat coverage

## Test plan
Coverage test as above (skipped without `redact` extra; runs in CI job "Tests (redact)"). Upload tests (synthetic STS files via `write(..., harness=)`, faked Hub): `--yes`; order (patched `input` reads stderr); `--allow-unredacted` with an unmarked claude.ai file; non-TTY abort; mixed directory count 2; singular wording; `count_harness` edge cases. Every success case asserts exit 0 and `create_commit` called. Offline.

## Risks
Privacy: warning only, defaults unchanged; fixture is fictional. Coverage changes show up as a test failure on a `uv.lock` bump of teich. No new dependencies. STS output unchanged.

## Open questions
None. (Gaps shown by measurement: file follow-ups for an LLM PII pass; out of scope here.)

## Size estimate
S
