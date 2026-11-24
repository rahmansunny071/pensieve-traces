# Design: Redaction pass, on by default (reuse teich's TraceAnonymizer behind [redact]) (#3)

## Problem
The canonical `Session` holds raw text, tool output and paths, possibly with secrets. Nothing removes them yet, so shareable output would leak. Users need a redaction pass that returns a scrubbed copy with consistent placeholders and fails loudly when it cannot run.

## Proposed approach
New `pensieve_traces/redact.py`; `teich` is imported lazily.
- `Anonymizer` Protocol: `anonymize_text(text: str) -> str`. teich 0.3.8's `TraceAnonymizer` has it and keeps per-instance maps, so one instance per session gives consistent replacements.
- `redact_session(session, anonymizer=None) -> Session` deep-copies, then redacts `cwd` (first), `project`, `title`, `git_branch`, each `Message.text` (tool results included), `reasoning`, `ToolCall.arguments` (kept as a string) and all strings in `Session.metadata` / `Message.metadata`. Ids, roles, timestamps, model, usage are untouched. The input is not mutated.
- If `anonymizer` is None, build `teich.anonymize.TraceAnonymizer()`. On `ImportError` raise `RedactionUnavailableError(ImportError)` with `pip install "pensieve-traces[redact]"`; never fall back to unredacted output.
- Writers and the CLI don't exist yet, so default-on and `--no-redact` (with warning) land with them. This issue exports `redact_session` and the error from `pensieve_traces`.
- pyproject.toml: add exactly `redact = ["teich>=0.3.8,<0.4"]` under optional-dependencies; `dependencies` stays `[]`. Regenerate `uv.lock` with `uv lock`. No workflow changes.

## Alternatives rejected
- Own regex redactor: duplicates teich's key formats.
- teich as a core dependency: pulls `datasets`, `fastapi`, `uvicorn` into every install.

## Files & interfaces touched
- NEW `pensieve_traces/redact.py`, `tests/test_redact.py`, `tests/fixtures/redact/secrets_session.json`
- CHANGED `pensieve_traces/__init__.py`, `pyproject.toml`, `uv.lock`, `README.md` (Privacy), `CHANGELOG.md`

## Test plan
Offline; synthetic fake secrets, fake email, fake home path (`/Users/jdoe`).
- CI installs only `--extra dev`, so core tests inject a stub `Anonymizer`: every field above passes through it, ids and usage are unchanged, the input is not mutated, `arguments` stays a string.
- Monkeypatched failing import: `RedactionUnavailableError` with the install hint, no Session returned.
- `pytest.importorskip("teich")` tests: fixture secrets, email and path are gone from `to_json()`, replacements are consistent, JSON `arguments` still parse. Skipped in CI.

## Risks
- Privacy: regex redaction is best-effort, and CI doesn't run real teich, so users should review output. Unredacted output must require an explicit opt-out later.
- Redaction could corrupt JSON in `arguments`; the real-teich test checks this.
- Spec conformance (STS / Hub viewer): none directly; placeholders stay plain strings.
- Dependencies: teich is optional, Apache-2.0, heavy, pinned `<0.4`; base install works without it.
- No instructions in the issue conflict with the process.

## Open questions
- Confirm default-on and `--no-redact` belong to the writer/CLI issues.
- Is `teich>=0.3.8,<0.4` right? Only 0.3.8 was inspected.
- Leave ids, `request_id` and `Session.id` unredacted? Unverified for harnesses other than Claude Code.

## Size estimate
S
