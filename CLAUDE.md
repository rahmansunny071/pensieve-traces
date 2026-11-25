# pensieve-traces — agent guide

Python ≥3.11 library and CLI that turns coding-agent session traces (Claude Code
first) into shareable formats and datasets: Hugging Face STS traces, OpenRouter
Broadcast traces, and ConvoMem-style memory datasets. Distribution
`pensieve-traces` → `import pensieve_traces`, CLI `pensieve-traces`.

Architecture (target): readers (claude_code, …) → one canonical `Session` model
→ redaction pass → writers (sts, openrouter, convomem) → builders (ConvoMem Q/A).
Every reader and writer goes through the canonical model; never convert
format-to-format directly.

## Release phase
mvp. In mvp, only blockers are fixed inside a PR (the main path breaks on realistic
input, private data leaks on realistic input, a hard-rule violation, irreversible loss or overwrite of
the user's existing data (source files, history, remote repos) on a plausible invocation, even a
mistaken one, when a cheap guard would prevent it, or a test that falsely passes);
everything else is filed as an `improvement` issue. Change this line to `hardening` before a first
formal release.

## Where knowledge lives
- **GitHub and the repo are the source of truth:** issues, PRs, review threads, labels, plans and
  implementation notes in `.factory/designs/`, and this file. Decisions and their reasons go here, where
  the maintainer and every agent can see them.
- **Each PR's "## Handoff" section** says what changed and why, the key decisions, how to test, and
  what's open. Keep it current after every change, so the next agent or reviewer starts from it.
- **The factory run log** (`.factory/runs/`, gitignored) is a local cache and cost ledger. It points at
  PRs, commits and threads and never holds decisions.
- **Questions for the maintainer** go on the PR as inline review comments, with options and a
  recommendation. Work continues elsewhere until they're answered.

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
- Never commit real agent traces, session files or chat exports. Tests use small,
  hand-written synthetic fixtures under tests/fixtures/ with no real paths,
  emails, names, secrets or private code.
- Never upload anything to the Hugging Face Hub, PyPI or any other service, and
  never ask for or use HF_TOKEN, PRIME_API_KEY or any other credential.
- Uploading is always intentional: only an explicit `pensieve-traces upload`
  run sends anything to the Hub. No other command, writer, builder, default or
  test uploads or contacts the Hub.
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
- Every code change ships its docs in the same PR: new docs for new behaviour,
  and updates to any doc it makes wrong or stale (README, CHANGELOG,
  docstrings, `.factory/designs/`). Docs that disagree with the PR's code break
  this rule, so they're a blocker even in `mvp`. Mismatches outside the PR's
  diff aren't fixed in it: file them as sub-issues of the "Documentation
  tech-debt" issue (#43).

## Style
- Match existing code style; ruff is the formatter of record.
- Small, reviewable diffs. No drive-by refactors.
- Pydantic or dataclasses for the canonical model; type hints everywhere.
