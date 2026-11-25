# Design: Chat PII redaction coverage and claude.ai upload warning (#48)

## Problem
Redaction (teich `TraceAnonymizer`) was tuned on coding sessions; claude.ai chats hold addresses, phones, health and money details. Nobody has measured what is caught, and `upload` says nothing when claude.ai traces are about to go to the Hub.

## Proposed approach
1. Measured with teich 0.3.8 (the `uv.lock` pin) on synthetic text; table below.
2. NEW `tests/fixtures/redact/chat_pii_session.json`: canonical `Session` (harness `claude.ai`), one fictional value per message, message id = kind.
3. NEW `tests/test_redact_chat_coverage.py`: `importorskip("teich.anonymize")`; a kind counts as replaced when its exact value is gone from that message's redacted text; asserts the fixture contains every value and the whole dict equals `EXPECTED_COVERAGE`.
4. NEW `upload.count_harness(files, harness) -> int`: first line only, exact `harness` match (`Claude.ai`, missing, empty, non-JSON, `[1]`, non-UTF-8 count 0, never raise). Shared `_read_header` helper also used by the redaction-marker check.
5. CHANGED `cli._upload`: after the summary, before the `--yes`/`_confirm()` branch, `_warn` if count > 0 (`HARNESS` imported from `readers.claude_ai`). Never refuses.

Warning text (`n` files, singular "1 file is"): `{n} files are claude.ai chats (harness "claude.ai"); redaction does not catch names, health details, money amounts or card and bank account numbers, and catches only some addresses and phone numbers. Review them before uploading.`

Pinned table (teich 0.3.8, measured):
| kind (value) | replaced |
|---|---|
| email (`jane.doe@vexlor-mail.invalid`) | yes |
| ssn_dashed (`987-65-4320`) | yes |
| phone_call_me_at_10_digit (`(555) 555-0142`) | yes |
| address_after_i_live_at (`42 Maple Lane`) | yes |
| phone_my_phone_is_7_digit (`555-0143`) | no |
| ssn_undashed (`987654320`) | no |
| address_send_it_to (`42 Maple Lane`) | no |
| iban (`GB82WEST12345698765432`) | no |
| card_number (`4111 1111 1111 1111`) | no |
| person_name (`Jane Doe`) | no |
| health_detail (`type 2 diabetes`) | no |
| money_amount (`$4,200`) | no |

Note: teich 0.3.8 replaces an address only after phrases like "I live at" or "address:", and phones depend on phrasing too, so coverage is trigger-phrase based.

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

## Implementation notes

- Built in PR #63: `upload.count_harness` (shared `_read_header`, first line only, exact `harness` match, odd files count 0) and a stderr warning in `cli._upload` after the summary and before the confirm/`--yes` branch; it never refuses. Coverage is pinned in `tests/test_redact_chat_coverage.py` over `tests/fixtures/redact/chat_pii_session.json` (teich 0.3.8, runs only in the redact CI job).
- Design review (6055546156): corrected measurement (some phones are caught), reserved `.invalid` email and other clearly fictional values, claims limited to the measured table.
- Code review (6059236668): address and SSN coverage depend on phrasing/format; rows split (`address_after_i_live_at` yes, `address_send_it_to` no; dashed SSN yes, undashed no) and the warning now says redaction "catches only some addresses and phone numbers" (1367921, verified).
- Follow-up: closing the measured gaps (LLM PII pass, review step, or extra patterns) → #66.
