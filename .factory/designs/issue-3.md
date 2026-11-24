# Design: Redaction pass, on by default (reuse teich's TraceAnonymizer behind [redact]) (#3)

## Problem
The canonical `Session` holds raw text, tool output and paths, possibly with secrets. Nothing removes them yet, so shareable output would leak. Users need a redaction pass that returns a scrubbed copy with consistent placeholders, an output gate that redacts by default, and a loud failure when redaction cannot run.

## Proposed approach
New `pensieve_traces/redact.py`; `teich` is imported lazily inside the function, and the original `ImportError` is chained (`raise ... from exc`) so a broken transitive dependency stays visible.
- `Anonymizer` Protocol: `anonymize_text(text: str) -> str` and `anonymize_value(value: Any) -> Any`. teich 0.3.8's `TraceAnonymizer` has both and keeps per-instance maps, so one instance per session gives consistent replacements.
- `redact_session(session, anonymizer=None) -> Session` deep-copies, then redacts `cwd` (first), `project`, `title`, `git_branch`, each `Message.text` (tool results included), `reasoning`, `ToolCall.arguments`, and `Session.metadata` / `Message.metadata` values (via `anonymize_value`). The input is not mutated.
- `ToolCall.arguments` (a JSON string): `json.loads` -> `anonymize_value` -> `json.dumps`; fall back to `anonymize_text` only if it isn't valid JSON. Why not `anonymize_text` on the raw JSON text: (a) an email after an escaped `\n` is kept, because the match starts at the `n` and the preceding `\` makes teich treat it as a non-email reference; (b) teich's bare-assignment pattern can capture the `\` before a closing quote (`"password=hunter2\""`), which breaks JSON escaping and leaves the secret, or eat a closing `}` after a numeric value; (c) key-aware rules only run on parsed objects. Decoding first keeps JSON valid by construction.
- `project`: if `project == basename(cwd)`, set the redacted `project` to the basename of the redacted cwd (so `cwd=/Users/jdoe` doesn't leak `jdoe`); otherwise redact it as text.
- Left unredacted by design: message ids, `parent_id`, `tool_call_id`, `ToolCall.id`, `request_id`, `Session.id`, `ToolCall.name`, `harness`, `model`, timestamps, usage, roles. Ids are UUIDs or `toolu_`/`req_`/`msg_` ids that teich treats as public, and #4/#5 need them.
- Anonymizer lifetime: if `anonymizer` is None, build a fresh `teich.anonymize.TraceAnonymizer()` per call. Sharing one injected anonymizer across sessions links their placeholders (documented in the docstring).
- `prepare_for_output(session, *, redact=True, anonymizer=None) -> Session` is the single output gate every writer must call.
  - `redact=True`: returns `redact_session(...)`; if teich is missing it raises `RedactionUnavailableError(ImportError)` with `pip install "pensieve-traces[redact]"`. It never falls back to unredacted output.
  - `redact=False`: returns the session unchanged and calls `warnings.warn("Redaction disabled: output may contain secrets, emails and local paths.", UserWarning, stacklevel=2)`.
  - This meets "redact by default; opt-out warns; missing extra fails clearly" in this issue, with tests. #4/#5/#7 only need to map a `--no-redact` flag to `redact=False`.
- Exports `redact_session`, `prepare_for_output` and `RedactionUnavailableError` from `pensieve_traces`.
- pyproject.toml: add exactly `redact = ["teich>=0.3.8,<0.4"]` under optional-dependencies; `dependencies` stays `[]`. Regenerate `uv.lock` with `uv lock` (expect a large diff: datasets, pyarrow, fastapi, ...; `--extra dev` installs none of it).
- CI: the separate `Tests (redact)` job (`uv sync --locked --extra dev --extra redact` + pytest) comes from a workflow PR the maintainer merges before the build. The build PR must not touch `.github/workflows/`.

## Alternatives rejected
- Own regex redactor: duplicates teich's key formats.
- teich as a core dependency: pulls `datasets`, `fastapi`, `uvicorn` into every install.
- `anonymize_text` on raw `arguments` JSON: leaks and corrupts (see above).

## Files & interfaces touched
- NEW `pensieve_traces/redact.py`, `tests/test_redact.py`, `tests/fixtures/redact/secrets_session.json`
- CHANGED `pensieve_traces/__init__.py`, `pyproject.toml`, `uv.lock`, `README.md` (Privacy), `CHANGELOG.md`

## Test plan
Offline; synthetic data only.
- Stateful stub anonymizer: one instance sees every field (text, reasoning, tool-call arguments via `anonymize_value`, tool results, titles, cwd, project, metadata). Ids, usage and `ToolCall.name` are unchanged, the input is not mutated, and `arguments` stays a valid JSON string even when the stub injects `"` and `\`.
- Fake `teich.anonymize` module (with `TraceAnonymizer`) put in `sys.modules` via monkeypatch: CI covers the default (non-injected) path and shows each `redact_session` call builds a fresh instance.
- Failing import (monkeypatched): `RedactionUnavailableError` with the install hint, `__cause__` set, no Session returned.
- `prepare_for_output`: `redact=True` redacts; `redact=True` without teich raises; `redact=False` returns the same session and emits the `UserWarning` with the exact text above (`pytest.warns`).
- Real teich (`pytest.importorskip("teich")`), run in CI by `Tests (redact)`: an email after an escaped `\n` in arguments; `"password=..."` inside a JSON string; a numeric secret value; `cwd="/Users/jdoe"` (neither cwd nor project contains `jdoe`); consistent placeholders across fields; arguments still parse. The builder also runs `uv sync --locked --extra dev --extra redact && uv run pytest -q tests/test_redact.py` locally and records the pass/skip counts in the PR.
- Builder notes: fixture emails must avoid teich's reserved domains (example.com/.org/.net, `.test`, `.local`); secret values must look random, not placeholder-like ("test", "fake", "example"). Don't commit realistic live-key shapes (`ghp_`, `sk-ant-`, `AKIA`, ...) that could trip GitHub push protection; build them at test runtime from fragments.

## Risks
- Privacy: regex redaction is best-effort; users should review output. Unredacted output requires the explicit `redact=False` opt-out, which warns.
- Later teich 0.3.x pattern changes: caught by the `Tests (redact)` job.
- Spec conformance (STS / Hub viewer): none directly; placeholders stay plain strings.
- Dependencies: teich is optional, Apache-2.0, heavy, pinned `<0.4`; base install works without it. `import teich.anonymize` loads the whole teich package, hence the lazy import.
- No instructions in the issue conflict with the process.

## Open questions
None.

## Size estimate
M
