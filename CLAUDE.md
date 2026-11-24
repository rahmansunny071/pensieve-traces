# pensieve-traces — agent guide

Python ≥3.11 library and CLI that turns coding-agent session traces (Claude Code
first) into shareable formats and datasets: Hugging Face STS traces, OpenRouter
Broadcast traces, and ConvoMem-style memory datasets. Distribution
`pensieve-traces` → `import pensieve_traces`, CLI `pensieve-traces`.

Architecture (target): readers (claude_code, …) → one canonical `Session` model
→ redaction pass → writers (sts, openrouter, convomem) → builders (ConvoMem Q/A).
Every reader and writer goes through the canonical model; never convert
format-to-format directly.

## Setup
uv sync --locked --extra dev    # same lockfile CI uses

## Definition of "checks pass" (identical to .github/workflows/ci.yml; run all, in order)
1. uvx ruff@0.17.4 format --check .         # ruff pinned to match CI
2. uvx ruff@0.17.4 check .
3. uv run --no-sync pytest -q               # offline: no network, no model, no real traces
4. If pyproject.toml defines the `redact` extra: uv sync --locked --extra dev --extra redact,
   then uv run --no-sync pytest -q again, so tests that need teich run instead of skipping
   (CI job "Tests (redact)"). Re-sync with --extra dev afterwards if you continue working.

If ci.yml changes, update this list to match. Never "fix" a failure by
changing lint rules, the ruff pin, or skipping tests.

## Hard rules
- Never commit real agent traces or session files, and never read
  ~/.claude, ~/.codex or any other host session directory. Tests use small,
  hand-written synthetic fixtures under tests/fixtures/ with no real paths,
  emails, names, secrets or private code.
- Never upload anything to the Hugging Face Hub, PyPI or any other service, and
  never ask for or use HF_TOKEN, PRIME_API_KEY or any other credential.
- Never call paid model endpoints. Code that needs an LLM (e.g. the ConvoMem
  Q/A builder) takes an injectable client, and tests use a fake one.
- Redaction must default to ON for anything that writes shareable output.
- New dependencies only when the approved design lists them. Heavy or
  optional ones (e.g. `teich`, `huggingface_hub`) go behind an extra
  (`pensieve-traces[redact]`, `[hub]`), and the base install must work without them.
- Never bump the version, publish a release, or edit release tooling unless the
  approved design says so explicitly.
- Never touch .github/workflows/ unless the approved design says so explicitly.
- Output formats must match their published specs exactly; cite the spec URL
  in the writer's module docstring:
  - STS: https://huggingface.co/docs/hub/main/en/session-traces-format
  - Agent traces on the Hub: https://huggingface.co/docs/hub/main/en/agent-traces
- Every user-visible change updates README.md and CHANGELOG.md.

## Style
- Match existing code style; ruff is the formatter of record.
- Small, reviewable diffs. No drive-by refactors.
- Pydantic or dataclasses for the canonical model; type hints everywhere.
