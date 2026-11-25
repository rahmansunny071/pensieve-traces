---
title: "Canonical model"
description: "The harness-neutral Session model every reader and writer goes through."
---

# Canonical model

`pensieve_traces.model` defines the harness-neutral dataclasses `Session`, `Message`, `ToolCall`, `Usage` and the `Role` enum (no extra dependencies). Timestamps are timezone-aware, tool arguments stay a raw JSON string, and `Session.to_json()` / `Session.from_json()` round-trip losslessly. The model holds raw text and does no redaction.

```python
from pathlib import Path

from pensieve_traces import Session

session = Session.from_json(Path("session.json").read_text())
session.tool_result_for("call-1")
```

Every reader and writer goes through this model; formats are never converted directly into each other. See the diagram on the [Overview](../index.md).
