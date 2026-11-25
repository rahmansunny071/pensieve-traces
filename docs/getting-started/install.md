---
title: "Install"
description: "Install pensieve-traces and its optional extras."
---

# Install

The base install has no dependencies. Optional extras:

```bash
pip install "pensieve-traces[redact]"   # redaction (uses teich); needed by convert and build
pip install "pensieve-traces[hub]"      # upload to the Hugging Face Hub
pip install "pensieve-traces[docs]"     # build this documentation site
```

Install the `redact` extra to enable redaction: it uses [teich](https://pypi.org/project/teich/)'s `TraceAnonymizer`. Redaction is on by default, so without the extra `convert` and `build convomem` exit with an error and write nothing (see [Redaction and privacy](../concepts/redaction.md)).

To work on the code instead, see [Development](../reference/development.md).
