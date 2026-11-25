---
title: "Topics"
description: "Tag conversations and items with topics using local matchers."
---

# Topics

`build convomem --topics topics.json` maps a topic name to local matchers:

```json
{"home-care": {"titles": ["caregiver"], "projects": ["demo"], "ids": []}}
```

`ids` match a session id exactly, `projects` the project name (the basename of the session's working directory; claude.ai chats have none) and `titles` a case-insensitive substring of the title. Matching conversations get `"topic": [...]` and items `"topics": [...]` (the union over their evidence conversations), extra keys convomem-arena ignores; no model labels topics. A malformed file is an error before any model call.
