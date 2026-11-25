---
title: "Install"
description: "Install pensieve-traces and its optional extras."
---

# Install

pensieve-traces is not on PyPI. It installs from GitHub, pinned to the `v0.1.0` release tag, so every command below names the repository URL.

The base install has no dependencies:

```bash
pip install "pensieve-traces @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"
```

Optional extras go in the brackets:

```bash
pip install "pensieve-traces[redact] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"   # redaction (uses teich); needed by convert and build
pip install "pensieve-traces[hub] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"      # upload to the Hugging Face Hub
pip install "pensieve-traces[docs] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"     # build this documentation site
```

With [uv](https://docs.astral.sh/uv/), the same source installs the CLI as a tool or into the current environment:

```bash
uv tool install "pensieve-traces[redact] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"
uv pip install "pensieve-traces[redact] @ git+https://github.com/rahmansunny071/pensieve-traces@v0.1.0"
```

Install the `redact` extra to enable redaction: it uses [teich](https://pypi.org/project/teich/)'s `TraceAnonymizer`. Redaction is on by default, so without the extra `convert` and `build convomem` exit with an error and write nothing (see [Redaction and privacy](../concepts/redaction.md)).

To work on the code instead, see [Development](../reference/development.md).
