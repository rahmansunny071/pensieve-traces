# Design: Release v0.1.0 prep (#86)
## Problem
The docs tell users to `pip install "pensieve-traces[redact]"`, but the package is not on PyPI, so the command fails. The version is still 0.0.0 and every changelog entry says "Unreleased:". The maintainer approved a GitHub-only v0.1.0 (phase stays mvp, version bump approved in the issue).
## Proposed approach
- `pyproject.toml` `version = "0.1.0"`; `uv lock` rewrites only the project's version in `uv.lock`.
- `CHANGELOG.md`: `# Changelog`, an empty `## Unreleased`, then `## 0.1.0 (2026-11-25)` holding all existing bullets, order and wording kept, only the "Unreleased: " prefix dropped. `docs/reference/changelog.md` includes the file from line 3, so both sections render.
- Install commands, pinned to the tag, extras kept:
  `pip install "pensieve-traces[redact] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"` (same for `[hub]`, `[docs]`, and the bare package). The install page states once that the package is not on PyPI and installs from GitHub, and also shows `uv tool install` / `uv pip install` forms. Other pages use the full pip form.
## Alternatives rejected
- Short `pip install "pensieve-traces[...]"` on other pages: fails without PyPI.
- Release automation or PyPI: out of scope.
## Files & interfaces touched
- CHANGED: `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `README.md`
- CHANGED: `docs/getting-started/{install,claude-code,claude-ai,convomem-dataset}.md`, `docs/guides/uploading.md`, `docs/concepts/redaction.md`, `docs/reference/development.md`
- NEW: this file
## Test plan
No code. `uv lock --check`, all CLAUDE.md checks, `mkdocs build --strict`, scratch-venv install from the pushed branch (`--help`, version 0.1.0).
## Risks
No privacy, spec or dependency impact. The tag `v0.1.0` does not exist until the orchestrator tags after merge, so the pinned commands work only then.
## Open questions
None
## Size estimate
S
