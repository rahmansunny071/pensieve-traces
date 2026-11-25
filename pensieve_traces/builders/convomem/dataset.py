"""ConvoMem dataset layout: writes evidence items, filler and the manifest, then moves them in.

Layout (https://huggingface.co/datasets/Salesforce/ConvoMem, as read by convomem-arena):

- ``core_benchmark/evidence_questions/<category folder>/<N>_evidence/me.json`` =
  ``{"evidence_items": [{question, answer, message_evidences: [{speaker, text}],
  conversations: [{id, containsEvidence: true, messages}]}]}``; folders without items are not
  created.
- ``core_benchmark/filler_conversations/me.json``: the filler-file layout of
  ``writers/convomem.py``. It holds only conversations that are evidence for no item: convomem-arena's
  ``mix()`` leaves an item's own evidence out of its filler, but its data check does not, so a
  filler file holding evidence conversations would make most fact and abstention items flag
  there. The cost is a smaller filler pool.
- ``build_manifest.json``: counts, hashes and settings only (no paths, base URL or key).

With topics, conversations get ``"topic"`` and items ``"topics"``, extra keys convomem-arena
ignores. This module does no redaction: pass already-redacted conversations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pensieve_traces.builders.convomem.items import CATEGORIES, EvidenceItem

__all__ = [
    "EVIDENCE_ROOT",
    "FILLER_ROOT",
    "MANIFEST_FILE",
    "CommitError",
    "build_manifest",
    "commit_dataset",
    "evidence_conversation_ids",
    "write_dataset",
]

EVIDENCE_ROOT = "core_benchmark/evidence_questions"
FILLER_ROOT = "core_benchmark/filler_conversations"
PERSONA_FILE = "me.json"
MANIFEST_FILE = "build_manifest.json"
MANIFEST_FORMAT_VERSION = 1


class CommitError(Exception):
    """The dataset could not be moved into place. ``kept`` is a temp dir left for recovery."""

    def __init__(self, message: str, kept: Path | None = None) -> None:
        super().__init__(message)
        self.kept = kept


def evidence_conversation_ids(items: list[EvidenceItem]) -> set[str]:
    return {cid for item in items for cid in item.conversation_ids}


def _dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_dataset(
    root: Path,
    items: list[EvidenceItem],
    conversations: list[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    topics_by_conversation: dict[str, list[str]] | None = None,
    topic_order: list[str] | None = None,
) -> None:
    """Write the layout into the existing directory ``root``. Never overwrites a file."""
    by_id = {c["id"]: c for c in conversations}
    order = {name: i for i, name in enumerate(topic_order or [])}

    def conv(cid: str, *, evidence: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": cid,
            "containsEvidence": evidence,
            "messages": by_id[cid]["messages"],
        }
        if topics_by_conversation is not None:
            out["topic"] = topics_by_conversation.get(cid, [])
        return out

    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for item in items:
        entry: dict[str, Any] = {
            "question": item.question,
            "answer": item.answer,
            "message_evidences": [{"speaker": e.speaker, "text": e.text} for e in item.evidence],
            "conversations": [conv(cid, evidence=True) for cid in item.conversation_ids],
        }
        if topics_by_conversation is not None:
            names = {
                n for cid in item.conversation_ids for n in topics_by_conversation.get(cid, [])
            }
            entry["topics"] = sorted(names, key=lambda n: order.get(n, len(order)))
        grouped.setdefault((CATEGORIES[item.category][0], item.evidence_count), []).append(entry)
    for (folder, count), entries in grouped.items():
        _dump(
            root / EVIDENCE_ROOT / folder / f"{count}_evidence" / PERSONA_FILE,
            {"evidence_items": entries},
        )

    used = evidence_conversation_ids(items)
    filler = [conv(c["id"], evidence=False) for c in conversations if c["id"] not in used]
    _dump(root / FILLER_ROOT / PERSONA_FILE, {"evidence_items": [{"conversations": filler}]})
    _dump(root / MANIFEST_FILE, manifest)


def build_manifest(
    *,
    inputs: int,
    sessions_read: int,
    duplicates_dropped: int,
    sessions_by_harness: dict[str, int],
    model: str,
    endpoint_local: bool,
    redaction: bool,
    prompt_hashes: dict[str, dict[str, str]],
    conversations: dict[str, int],
    items_by_category: dict[str, int],
    extract_stats: dict[str, int],
    item_stats: dict[str, Any],
    topics_enabled: bool,
    topics_count: int,
) -> dict[str, Any]:
    """The ``build_manifest.json`` content. Counts and settings only."""
    return {
        "format_version": MANIFEST_FORMAT_VERSION,
        "inputs": {
            "count": inputs,
            "sessions_read": sessions_read,
            "duplicates_dropped": duplicates_dropped,
            "sessions_by_harness": sessions_by_harness,
        },
        "model": model,
        "endpoint_local": endpoint_local,
        "redaction": redaction,
        "prompt_hashes": prompt_hashes,
        "conversations": conversations,
        "items_by_category": items_by_category,
        "extract_stats": extract_stats,
        "item_stats": item_stats,
        "topics": {"enabled": topics_enabled, "count": topics_count},
    }


def commit_dataset(tmp: Path, out: Path) -> None:
    """Move the dataset in ``tmp`` to ``out`` without replacing anything.

    ``out`` absent: ``tmp`` is renamed to it. ``out`` an existing directory: ``core_benchmark/``
    is renamed in, then the manifest is created there with mode "x"; if the second step fails
    ``core_benchmark/`` is moved back. Raises :class:`CommitError`; ``kept`` is set when the
    rollback failed and ``tmp`` must not be deleted.
    """
    if not os.path.lexists(out):
        try:
            os.rename(tmp, out)
        except OSError as exc:
            raise CommitError(f"could not move the dataset to {out}: {exc.strerror}") from exc
        return
    core, manifest = out / "core_benchmark", out / MANIFEST_FILE
    if os.path.lexists(core) or os.path.lexists(manifest):
        raise CommitError(f"{out} now holds core_benchmark/ or {MANIFEST_FILE}; nothing replaced")
    try:
        os.rename(tmp / "core_benchmark", core)
    except OSError as exc:
        raise CommitError(f"could not move core_benchmark/ into {out}: {exc.strerror}") from exc
    try:
        with open(manifest, "x", encoding="utf-8", newline="\n") as fh:
            fh.write((tmp / MANIFEST_FILE).read_text(encoding="utf-8"))
    except BaseException as exc:
        # Only remove a manifest this call created (a FileExistsError means it is not ours).
        if not isinstance(exc, FileExistsError):
            try:
                manifest.unlink()
            except OSError:
                pass
        try:
            os.rename(core, tmp / "core_benchmark")
        except OSError:
            raise CommitError(
                f"could not write {MANIFEST_FILE} and could not move core_benchmark/ back; "
                f"core_benchmark/ stays in {core}; the temp dir {tmp} was kept",
                kept=tmp,
            ) from exc
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise CommitError(f"could not write {MANIFEST_FILE} into {out}; nothing replaced") from exc
