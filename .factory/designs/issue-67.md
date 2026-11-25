# Design: Redact an email address followed by a period (#67)

## Problem
With teich 0.3.8, an email directly followed by `.` is left unredacted (`mail jordan.doe@example.invalid.`), so a sentence-ending address reaches every writer and the model in `build convomem`.

## Proposed approach
Measured on synthetic text with `TraceAnonymizer`: the email is replaced before ` `, `,`, `!`, `?`, `:`, `;`, `)`, `'s`; it is NOT replaced when followed by `.` (also `..`, `.)`, `."`, `.\n`, end of text). It IS replaced in `x@y.invalid.com` / `x@y.invalid.Next` (dot plus letters is read as domain). Placeholders (`redacted-userN@example.com`) are keyed by address inside the anonymizer, and teich leaves an existing placeholder alone, even when `.` follows it.

In `redact.py`, NEW private `_EmailPeriodGuard` wraps whichever `Anonymizer` is in use (injected or default) and `redact_session` uses the wrapper for every call (cwd, project, title, branch, metadata, text, reasoning, arguments).
- `anonymize_text(t)`: replace each match of `_EMAIL_DOT = (?<![\w.%+-])([\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)(\.+)(?=$|[\s"')\]}>,;:!?])` with `inner.anonymize_text(group1) + group2`, then pass the result to `inner.anonymize_text`. Delegating the address to the same inner anonymizer reuses its address-to-placeholder map, so the same address gets one placeholder anywhere in a session and across sessions sharing one anonymizer.
- `anonymize_value(v)`: walk dicts (values), lists, tuples; apply the pre-pass to strings; then `inner.anonymize_value`. Dict keys are untouched (as before).
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
Without teich: a fake anonymizer (own address map, `redacted-userN`) proves the pre-pass for `.`, `.)`, `."`, `.` plus newline, end of text, `..`; same placeholder for the address with and without a period, across two sessions sharing one fake; value/metadata/arguments covered; `a@b.invalid.com` and non-email text with periods untouched. With teich (importorskip, redact CI job): new pinned rows `email_period`, `email_period_paren`, `email_period_quote`, `email_exclaim`, `email_question`, `email_colon`, `email_semicolon` all replaced, plus a same-placeholder check against the bare address. Offline.

## Risks
Privacy: strictly widens redaction; the lookahead is deliberately conservative. Over-matching only turns an address into a placeholder. No new dependency; teich untouched.

## Open questions
- Dict keys in metadata containing an email-plus-period are not redacted (already true for bare emails); out of scope.
- `jordan@x.invalid.Next` is caught by teich only because it swallows `.Next`; unverified for other suffix shapes.

## Size estimate
S

Docs updated: README "Redaction" (email row notes trailing punctuation covered), CHANGELOG, `redact.py` docstring, this design's notes.
