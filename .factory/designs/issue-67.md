# Design: Redact an email address followed by a period (#67)

## Problem
With teich 0.3.8, an email directly followed by `.` is left unredacted (`mail jordan.doe@example.invalid.`), so a sentence-ending address reaches every writer and the model in `build convomem`.

## Proposed approach
Measured on synthetic text with `TraceAnonymizer`: the email is replaced before ` `, `,`, `!`, `?`, `:`, `;`, `)`, `'s`; it is NOT replaced when followed by `.` (also `..`, `.)`, `."`, `.\n`, end of text). It IS replaced in `x@y.invalid.com` / `x@y.invalid.Next` (dot plus letters is read as domain). Placeholders (`redacted-userN@example.com`) are keyed by address inside the anonymizer, and teich leaves an existing placeholder alone, even when `.` follows it.

In `redact.py`, NEW private `_EmailPeriodGuard` wraps whichever `Anonymizer` is in use (injected or default) and `redact_session` uses the wrapper for every call (cwd, project, title, branch, metadata, text, reasoning, arguments).
- `anonymize_text(t)`: if `"@" not in t` skip (as teich does); else replace each match of `_EMAIL_DOT` with `inner.anonymize_text(address) + dots`, then pass the result to `inner.anonymize_text`. `_EMAIL_DOT` = teich's local part with its lookbehind, `(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*` (the address), then `(\.+)(?![A-Za-z0-9_%+-])` (teich's boundary without the dot). So `.Next`/`.com` stay with teich, while curly quotes, backticks, `.*`, `.|`, `.<br>` are caught. The address handed to teich is exactly what teich matches without the period, so the placeholder is the same one (`o'brien@x.invalid.` leaves nothing behind). Delegating to the same inner anonymizer reuses its address map across a session and across sessions sharing one anonymizer.
- `anonymize_value(v)`: walk dicts and lists; pre-pass string values and string keys (teich redacts emails in keys); rebuild the dict without mutating the input, so teich's key-aware rules (`password`, personal-data keys, assistant content, base64 `data`) still see the original key names. No tuple branch. Then `inner.anonymize_value`.
- `_redact_arguments` keeps its logic on the wrapper, so JSON arguments are covered.

## Alternatives rejected
- Post-pass on teich output: it would see placeholders followed by `.` and need exclusions.
- Reimplement all email redaction: duplicates teich and changes numbering.

## Files & interfaces touched
- CHANGED pensieve_traces/redact.py (`_EmailPeriodGuard`, `_EMAIL_DOT`; public API unchanged)
- CHANGED tests/test_redact.py (fake-anonymizer tests, no teich)
- CHANGED tests/test_redact_chat_coverage.py, tests/fixtures/redact/chat_pii_session.json (new rows)
- CHANGED README.md (Redaction table), CHANGELOG.md, redact.py docstring

## Test plan
Without teich: a fake anonymizer (lower-cased address map, `redacted-userN`, leaves `@example.com` alone; asserted once to leave `x@y.invalid.` unchanged by itself, like teich) proves the pre-pass for `.`, `.)`, `."`, `.` plus newline, end of text, `..`; same placeholder for the address with and without a period, across two sessions sharing one fake; value/metadata/arguments covered; `a@b.invalid.com` and non-email text with periods untouched. With teich (importorskip, redact CI job): new pinned rows `email_period`, `email_period_paren`, `email_period_quote`, `email_exclaim`, `email_question`, `email_colon`, `email_semicolon` (regression guards) and `email_curly_quote` all replaced, plus a same-placeholder check against the bare address's placeholder (not hard-coded user numbers). Offline.

## Risks
Privacy: strictly widens redaction; the lookahead is deliberately conservative. Over-matching only turns an address into a placeholder. No new dependency; teich untouched.

## Open questions
- None open: dict keys are now pre-passed too (design review).
- Out of scope and not claimed in the README: a single letter or digit right after the period (`x@y.invalid.I will`, `.5`) is missed by both teich and the pre-pass.

## Size estimate
S

Docs updated: README "Redaction" (the intro sentence now says the table measures `redact_session`, teich plus the pre-pass; email row names the measured followers), the `test_redact_chat_coverage.py` docstring and both `redact.py` docstrings, CHANGELOG, `redact.py` docstring, this design's notes.
