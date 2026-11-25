---
title: "Redaction and privacy"
description: "What redaction does, how it is applied, and the measured coverage on chat content."
---

# Redaction and privacy

!!! warning "Privacy"
    Traces can contain prompts, tool output, local paths, secrets, private code and personal data. Redaction is on by default, but review anything before you publish it, and keep datasets private when unsure.

Install the optional extra to enable redaction: `pip install "pensieve-traces[redact] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"` (uses [teich](https://pypi.org/project/teich/)'s `TraceAnonymizer`; the base install has no dependencies).

```python
from pensieve_traces import prepare_for_output, redact_session

safe = prepare_for_output(session)  # redacts by default; raises if teich is missing
raw = prepare_for_output(session, redact=False)  # explicit opt-out, emits a UserWarning
copy = redact_session(session)  # scrubbed deep copy, input untouched
```

`build convomem` also redacts through `prepare_for_output`, once over all inputs, before any model call. Writers must obtain their session through `prepare_for_output`. It never falls back to unredacted output: without the extra it raises `RedactionUnavailableError`. Ids, tool names, timestamps and usage are left as is. Redaction is pattern-based and best-effort, so review output before sharing.

## What redaction covers on chat content

teich was tuned on coding sessions. Measured on `redact_session` (teich 0.3.8, the version `uv.lock` pins, plus a small pre-pass that also replaces an email address directly followed by a period) on a synthetic claude.ai-style chat (`tests/fixtures/redact/chat_pii_session.json`), pinned by `tests/test_redact_chat_coverage.py` so a teich upgrade that changes coverage shows up as a test change:

| Kind | Replaced |
|---|---|
| email address, also when followed by `.`, `.)`, `."`, a curly quote, `!`, `?`, `:` or `;` | yes |
| dashed SSN-style number (`987-65-4320`) | yes |
| street address after "I live at" | yes |
| 10-digit phone after a phrase like "Call me at" | yes |
| undashed SSN-style number (`My SSN is 987654320.`) | no |
| street address after other phrasing ("Send it to 42 Maple Lane, Springfield.") | no |
| 7-digit phone after other phrasing ("My phone is 555-0143") | no |
| IBAN | no |
| card number | no |
| person name | no |
| health detail | no |
| money amount | no |

Not covered: an address followed by a period and then a letter, digit, `-`, `_`, `%` or `+` with no space (`name@host.invalid.I will`, `.5`, `.2024`, `.-x`), which teich also misses. Phone and address matching depends on the surrounding phrasing (teich 0.3.8 replaces an address only after phrases like "I live at" or "address:"; "My address is …", "Send it to …" and "Deliver to …" are left as is). Redaction does not catch chat personal data in general: review claude.ai traces before sharing them.
