"""Structure check for a generated ConvoMem dataset (``pensieve-traces check-convomem``).

Mirrors what convomem-arena reads (``EVIDENCE_ROOT``, ``FILLER_ROOT``, ``CATEGORIES`` and their
evidence counts, ``parse_evidence_file``, ``parse_filler_file``) and its ``check_structure``
rule that evidence comes from distinct conversations. Content checks (answer grounding,
duplicates, filler contamination by words) stay in convomem-arena's data check.

Reads only the dataset directory and writes nothing. Content problems are returned as
:class:`Problem` values, never raised. Problems name files, item indexes and reasons, never
message text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pensieve_traces.builders.convomem.dataset import EVIDENCE_ROOT, FILLER_ROOT
from pensieve_traces.builders.convomem.items import CATEGORIES

__all__ = ["Problem", "check_dataset", "summarize"]

_COUNT_DIR = re.compile(r"(\d+)_evidence")
_FOLDER_TO_CATEGORY = {folder: cat for cat, (folder, _) in CATEGORIES.items()}


@dataclass(frozen=True)
class Problem:
    """One structural problem. ``file`` is relative to the dataset directory; ``item`` is the
    0-based index in the file's ``evidence_items`` (None when the problem is about the file)."""

    file: str
    item: int | None
    reason: str

    def __str__(self) -> str:
        where = self.file if self.item is None else f"{self.file}[item {self.item}]"
        return f"{where}: {self.reason}"


def _load(path: Path, rel: str, problems: list[Problem]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        reason = "not valid JSON" if isinstance(exc, ValueError) else f"unreadable: {exc.strerror}"
        problems.append(Problem(rel, None, reason))
        return None


def _conversations(item: dict[str, Any]) -> list[dict[str, Any]]:
    convs = item.get("conversations")
    return [c for c in convs if isinstance(c, dict)] if isinstance(convs, list) else []


def _message_texts(conv: dict[str, Any]) -> set[str]:
    msgs = conv.get("messages")
    if not isinstance(msgs, list):
        return set()
    return {m["text"] for m in msgs if isinstance(m, dict) and isinstance(m.get("text"), str)}


def _distinct_assignment(candidates: list[set[int]]) -> bool:
    """True if each evidence can be given its own conversation (bipartite matching)."""
    owner: dict[int, int] = {}

    def place(i: int, seen: set[int]) -> bool:
        for c in candidates[i]:
            if c in seen:
                continue
            seen.add(c)
            if c not in owner or place(owner[c], seen):
                owner[c] = i
                return True
        return False

    return all(place(i, set()) for i in range(len(candidates)))


def _in_order(texts: list[str], convs: list[dict[str, Any]]) -> bool:
    """Greedy: each evidence text in a later conversation than the previous one."""
    sets = [_message_texts(c) for c in convs]
    prev = -1
    for text in texts:
        nxt = next((i for i in range(prev + 1, len(sets)) if text in sets[i]), None)
        if nxt is None:
            return False
        prev = nxt
    return True


def _check_item(
    rel: str, index: int, raw: Any, category: str, count: int, problems: list[Problem]
) -> set[str]:
    """Check one item; return the ids of its conversations marked as evidence."""

    def bad(reason: str) -> None:
        problems.append(Problem(rel, index, reason))

    if not isinstance(raw, dict):
        bad("item is not an object")
        return set()
    for key in ("question", "answer"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            bad(f"{key} missing or empty")
    convs = _conversations(raw)
    if not convs:
        bad("conversations missing or empty")
    ev = raw.get("message_evidences")
    if not isinstance(ev, list):
        bad("message_evidences missing or not a list")
        ev = []
    elif len(ev) != count:
        bad(f"message_evidences has {len(ev)} entries, folder says {count}")
    marked = [c for c in convs if c.get("containsEvidence") is True]
    if len(ev) != len(marked):
        bad(
            f"message_evidences has {len(ev)} entries but {len(marked)} conversations "
            "are marked containsEvidence"
        )
    texts: list[str] = []
    found = True
    for e in ev:
        text = e.get("text") if isinstance(e, dict) else None
        if not isinstance(text, str) or not text:
            bad("message_evidences entry has no text")
            found = False
            continue
        texts.append(text)
        if not any(text in _message_texts(c) for c in convs):
            bad("evidence text is not a message of the item's conversations")
            found = False
    if found and texts:
        cands = [{i for i, c in enumerate(marked) if t in _message_texts(c)} for t in texts]
        if not _distinct_assignment(cands):
            bad("evidence does not come from distinct conversations")
        elif category == "changing_facts" and not _in_order(texts, marked):
            bad("changing evidence is not in conversation order")
    return {c["id"] for c in marked if isinstance(c.get("id"), str)}


def check_dataset(path: str | Path) -> list[Problem]:
    """Check the structure of the dataset directory ``path``; ``[]`` means it is valid."""
    root = Path(path)
    problems: list[Problem] = []
    base = root / EVIDENCE_ROOT
    if not base.is_dir():
        return [Problem(EVIDENCE_ROOT, None, "evidence directory not found")]
    used_by_file: dict[str, set[str]] = {}
    for cat_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        category = _FOLDER_TO_CATEGORY.get(cat_dir.name)
        rel_cat = f"{EVIDENCE_ROOT}/{cat_dir.name}"
        if category is None:
            problems.append(Problem(rel_cat, None, "unknown category folder"))
            continue
        for count_dir in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
            m = _COUNT_DIR.fullmatch(count_dir.name)
            rel_dir = f"{rel_cat}/{count_dir.name}"
            if m is None:
                problems.append(Problem(rel_dir, None, "folder is not <N>_evidence"))
                continue
            count = int(m.group(1))
            allowed = CATEGORIES[category][1]
            if count not in allowed:
                problems.append(
                    Problem(
                        rel_dir,
                        None,
                        f"evidence count {count} not allowed (allowed: "
                        f"{', '.join(map(str, allowed))})",
                    )
                )
                continue
            for f in sorted(count_dir.glob("*.json")):
                rel = f"{rel_dir}/{f.name}"
                ids = used_by_file.setdefault(f.name, set())
                payload = _load(f, rel, problems)
                if payload is None:
                    continue
                items = payload.get("evidence_items") if isinstance(payload, dict) else None
                if not isinstance(items, list):
                    problems.append(Problem(rel, None, 'no "evidence_items" list'))
                    continue
                for i, raw in enumerate(items):
                    ids |= _check_item(rel, i, raw, category, count, problems)
    fill_base = root / FILLER_ROOT
    for name, used in sorted(used_by_file.items()):
        rel = f"{FILLER_ROOT}/{name}"
        if not (fill_base / name).is_file():
            problems.append(Problem(rel, None, "filler file missing for evidence file"))
            continue
        payload = _load(fill_base / name, rel, problems)
        if payload is None:
            continue
        try:
            filler_ids = [
                c.get("id") for it in payload["evidence_items"] for c in it["conversations"]
            ]
        except (KeyError, TypeError, AttributeError):
            problems.append(Problem(rel, None, "not the filler layout"))
            continue
        clash = {i for i in filler_ids if isinstance(i, str) and i in used}
        if clash:
            problems.append(
                Problem(rel, None, f"{len(clash)} evidence conversation(s) in the filler file")
            )
    return problems


def summarize(path: str | Path) -> dict[str, tuple[int, int]]:
    """Per category folder: (number of evidence files, number of items). Skips unreadable files."""
    out: dict[str, tuple[int, int]] = {}
    base = Path(path) / EVIDENCE_ROOT
    if not base.is_dir():
        return out
    for cat_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if cat_dir.name not in _FOLDER_TO_CATEGORY:
            continue
        files = items = 0
        for f in sorted(cat_dir.glob("*_evidence/*.json")):
            files += 1
            try:
                items += len(json.loads(f.read_text(encoding="utf-8"))["evidence_items"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        out[cat_dir.name] = (files, items)
    return out
