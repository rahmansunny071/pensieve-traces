---
title: "Development"
description: "Set up, check and preview the docs locally."
---

# Development

```bash
uv sync --locked --extra dev
uvx ruff@0.17.4 format --check . && uvx ruff@0.17.4 check .
uv run --no-sync pytest -q
```

## This documentation

```bash
uv sync --locked --extra dev --extra docs   # or: pip install "pensieve-traces[docs] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"
uv run --no-sync mkdocs serve                # local preview
uv run --no-sync mkdocs build --strict       # fails on any warning, such as a broken link
```

The site is published to <https://rahmansunny071.github.io/pensieve-traces/> by `.github/workflows/docs.yml` on pushes to `main` that touch the docs. The [changelog](changelog.md) is rendered from `CHANGELOG.md`, not copied.
