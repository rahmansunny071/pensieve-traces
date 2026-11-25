---
title: "Checking a ConvoMem dataset"
description: "Check the structure of a generated dataset, and run convomem-arena's full check."
---

# Checking a ConvoMem dataset

```bash
pensieve-traces check-convomem dataset/
```

The structure check needs no extra dependency, reads only `dataset/` and writes nothing. It prints the files and items per category folder, then every problem as `file[item i]: reason`, and exits 1 if there is any problem (0 and `ok` otherwise). It checks that:

- only known category folders and `<N>_evidence` folders with N in that category's range exist;
- every evidence file is `{"evidence_items": [...]}` and each item has a non-empty `question`, `answer` and `conversations`, and `message_evidences` of length N;
- each `message_evidences[].text` is, verbatim, a message of the item's conversations, and the evidence comes from distinct conversations (as many as the conversations marked `containsEvidence`);
- `changing_evidence` evidence appears in conversation order;
- each evidence file has a filler file of the same name, and no evidence conversation is in it.

From Python: `pensieve_traces.builders.convomem.check_dataset(path)` returns a list of `Problem(file, item, reason)`. Content checks (answer grounding, duplicates, filler contamination) are not done here: they belong to [convomem-arena](https://github.com/rahmansunny071/convomem-arena)'s data check. Once [convomem-arena#18](https://github.com/rahmansunny071/convomem-arena/issues/18) lands, that check and the taskset can run on a local folder: run `python -m convomem_arena.data_check --local dataset/ --out reports/`, and point the taskset at `dataset/` as its local root (see #18 for the setting). Until then they read only the Hub dataset.
