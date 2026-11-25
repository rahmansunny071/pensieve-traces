"""Turn candidate facts into ConvoMem evidence items for the six categories.

Step 2 of 3 of the ConvoMem dataset builder (:func:`~pensieve_traces.builders.convomem.extract_candidates`
finds the facts; writing files is a later step). Output format and category names follow
https://huggingface.co/datasets/Salesforce/ConvoMem and convomem-arena's ``CATEGORIES``.

PRIVACY: this module does not redact. Evidence conversations, facts and questions are sent to the
endpoint behind ``client``. The caller must pass sessions that already went through
:func:`~pensieve_traces.prepare_for_output`. The ``sessions`` must be the same ones the candidates
were extracted from.

How an item is made (grouping by code, wording by the model):

* ``user_facts``, ``assistant_facts``, ``preferences``, ``implicit_connections``, ``abstention``:
  the model proposes groups of candidate numbers from their statements (``group_<category>.txt``);
  code checks each group's indices and its evidence count. The candidates are cut into chunks of
  ``group_chunk``, in input order, and a group never spans a chunk boundary.
* ``changing_facts``: code only. ``changing_value`` candidates with the same normalized
  ``subject``, sorted by the timestamp of their first message, equal neighbouring values merged,
  the latest 6 kept, at least 2 needed. A group with a missing timestamp is dropped.
* The model then writes the question and the answer (``items_<category>.txt``). It never writes
  evidence: the evidence messages are the candidates' verbatim messages. Preferences and implicit
  connections answers are rubrics; abstention answers are model-written.

Checks, each failure drops the item and bumps one :class:`Stats` counter: every evidence message
comes from a different conversation (``evidence_count == len(conversation_ids)``, as ConvoMem's
validator and convomem-arena require; checked before any wording call); evidence equals a full
message of its conversation; chronology of changing facts; the answer of a changing fact contains
its last value (numbers compared as numbers, so "$1,500" holds 1500); no question duplicates an earlier kept one; no other conversation gives the answer
away (convomem-arena's ``check_filler``, see ``_text.py``); and, with ``verify=True``, a model
answers the question from the evidence conversations only and a judge prompt (copied from
ConvoMem's ``AnsweringEvaluation.scala``) must say RIGHT. Abstention passes when the model
abstains.

Prompts are adapted from Salesforce ConvoMem (Apache-2.0); see ``prompts/`` and the repo ``NOTICE``.

Cache: with ``cache_dir`` every model call (groups, wording, answers, judges) is cached exactly as in
:func:`~pensieve_traces.builders.convomem.extract_candidates`: same directory rules, keyed on the
exact request messages and ``client.model``. Free-text replies are stored as a one-element list. A
rerun after a crash makes no repeated call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any

from pensieve_traces.builders.convomem._common import (
    cache_path,
    cache_read,
    cache_write,
    open_cache_dir,
    parse_reply,
    strip_think,
)
from pensieve_traces.builders.convomem._text import (
    content_words,
    is_number,
    norm,
    numbers,
    specifics,
    words,
)
from pensieve_traces.builders.convomem.extract import PROMPT_DELIMITER, Candidate
from pensieve_traces.builders.llm import ChatClient, ModelClientError
from pensieve_traces.model import Session
from pensieve_traces.writers.convomem import sessions_to_conversations_with_ids

__all__ = [
    "CATEGORIES",
    "ITEM_PROMPT_FILES",
    "EvidenceItem",
    "EvidenceMessage",
    "Stats",
    "build_items",
    "item_prompt_hashes",
]

log = logging.getLogger(__name__)

# category -> (dataset folder, allowed evidence counts); same as convomem-arena data.py CATEGORIES.
CATEGORIES: dict[str, tuple[str, tuple[int, ...]]] = {
    "user_facts": ("user_evidence", (1, 2, 3, 4, 5, 6)),
    "assistant_facts": ("assistant_facts_evidence", (1, 2, 3, 4, 5, 6)),
    "changing_facts": ("changing_evidence", (2, 3, 4, 5, 6)),
    "abstention": ("abstention_evidence", (1, 2, 3)),
    "preferences": ("preference_evidence", (1, 2)),
    "implicit_connections": ("implicit_connection_evidence", (1, 2, 3)),
}
# Candidate kinds each grouped category draws from (changing_facts is built by code).
_POOLS: dict[str, tuple[str, ...]] = {
    "user_facts": ("user_fact",),
    "assistant_facts": ("assistant_fact",),
    "preferences": ("preference",),
    "implicit_connections": ("user_fact", "preference"),
    "abstention": ("user_fact", "assistant_fact", "preference"),
}
_FACT_CATEGORIES = ("user_facts", "assistant_facts", "changing_facts")
_RUBRIC_CATEGORIES = ("preferences", "implicit_connections")
FILLER_THRESHOLD = 0.8
ITEM_PROMPT_FILES = (
    *(f"group_{c}.txt" for c in _POOLS),
    *(f"items_{c}.txt" for c in CATEGORIES),
    "verify_answer.txt",
    "judge_default.txt",
    "judge_rubric.txt",
    "judge_user_facts_single.txt",
    "judge_user_facts_multi.txt",
    "judge_abstention.txt",
)
_PROMPTS = resources.files("pensieve_traces.builders.convomem") / "prompts"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_VERDICT = re.compile(r"\W*(RIGHT|WRONG)\b", re.IGNORECASE)
_MAX_CHANGING = 6


@dataclass(frozen=True)
class EvidenceMessage:
    """One evidence message: the full, verbatim message of a conversation."""

    conversation_id: str
    message_id: str
    speaker: str
    text: str


@dataclass
class EvidenceItem:
    category: str
    evidence_count: int  # number of evidence messages, the ``<N>_evidence`` folder
    question: str
    answer: str
    evidence: list[EvidenceMessage]
    conversation_ids: list[str]  # the evidence conversations, in evidence order

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Stats:
    model_calls: int = 0
    cache_hits: int = 0
    failed_calls: int = 0  # model calls that raised ModelClientError
    groups_proposed: int = 0
    dropped_bad_json: int = 0  # a JSON reply that could not be parsed
    dropped_bad_group: int = 0  # bad indices, wrong size, or evidence count out of range
    dropped_missing_fields: int = 0  # wording reply without a usable question and answer
    dropped_shared_conversation: int = 0  # two evidence messages in one conversation
    dropped_evidence_not_verbatim: int = 0
    dropped_chronology: int = 0
    dropped_answer_mismatch: int = 0
    dropped_duplicate_question: int = 0
    dropped_answer_in_filler: int = 0
    dropped_topic_in_filler: int = 0
    dropped_verification: int = 0
    items: int = 0
    items_by_category: dict[str, int] = field(default_factory=lambda: dict.fromkeys(CATEGORIES, 0))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load(name: str) -> str:
    text = (_PROMPTS / name).read_text(encoding="utf-8")
    _, sep, body = text.partition(PROMPT_DELIMITER + "\n")
    if not sep:
        raise ValueError(f"prompt {name} has no {PROMPT_DELIMITER} line")
    return body


def item_prompt_hashes() -> dict[str, str]:
    """SHA-256 of each prompt file added by this step, keyed by file name (like ``prompt_hashes``)."""
    return {n: hashlib.sha256((_PROMPTS / n).read_bytes()).hexdigest() for n in ITEM_PROMPT_FILES}


def _fill(template: str, **values: str) -> str:
    """Single-pass ``{name}`` substitution, so a value that contains ``{other}`` stays as text."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


class _Run:
    def __init__(
        self,
        client: ChatClient,
        sessions: list[Session],
        cache_dir: str | os.PathLike[str] | None,
        stats: Stats,
    ) -> None:
        self.client = client
        self.stats = stats
        self.cache = open_cache_dir(cache_dir)
        conversations, id_map = sessions_to_conversations_with_ids(sessions)
        self.order = [c["id"] for c in conversations]
        self.messages: dict[str, list[dict[str, str]]] = {
            c["id"]: c["messages"] for c in conversations
        }
        self.position = {
            cid: {mid: i for i, mid in reversed(list(enumerate(ids)))}
            for cid, ids in id_map.items()
        }
        self._filler: list[tuple[str, set[str], set[str]]] | None = None

    def ask(self, prompt: str, *, json_reply: bool) -> list[Any] | None:
        """One cached model call; ``None`` after a failed call or an unparseable JSON reply."""
        request = [{"role": "user", "content": prompt}]
        path = cache_path(self.cache, request, self.client.model) if self.cache else None
        reply = cache_read(path) if path else None
        if reply is not None:
            self.stats.cache_hits += 1
            return reply
        self.stats.model_calls += 1
        try:
            raw = self.client.chat(request)
        except ModelClientError:
            self.stats.failed_calls += 1
            return None
        if json_reply:
            try:
                reply = parse_reply(raw)
            except (ValueError, TypeError):
                self.stats.dropped_bad_json += 1
                return None
        else:
            reply = [strip_think(raw)]
        if path:
            cache_write(path, reply)
        return reply

    def verbatim(self, ev: EvidenceMessage) -> bool:
        i = self.position.get(ev.conversation_id, {}).get(ev.message_id)
        if i is None:
            return False
        m = self.messages[ev.conversation_id][i]
        return m["text"] == ev.text and m["speaker"] == ev.speaker

    def filler(self, evidence_ids: set[str]) -> list[tuple[str, set[str], set[str]]]:
        if self._filler is None:
            self._filler = [
                (cid, set(words(m["text"])), numbers(m["text"]))
                for cid in self.order
                for m in self.messages[cid]
            ]
        return [f for f in self._filler if f[0] not in evidence_ids]


def _evidence_of(group: list[Candidate]) -> list[EvidenceMessage]:
    seen: set[tuple[str, str]] = set()
    out: list[EvidenceMessage] = []
    for c in group:
        for mid, speaker, text in zip(c.message_ids, c.speakers, c.texts, strict=False):
            if (c.conversation_id, mid) not in seen:
                seen.add((c.conversation_id, mid))
                out.append(EvidenceMessage(c.conversation_id, mid, speaker, text))
    return out


def _facts_text(group: list[Candidate]) -> str:
    blocks = []
    for n, c in enumerate(group, 1):
        lines = [f"Fact {n}: {c.statement}"]
        lines += [
            f"  Evidence message ({s}): {t}" for s, t in zip(c.speakers, c.texts, strict=False)
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _model_groups(
    run: _Run, category: str, pool: list[Candidate], chunk: int
) -> list[list[Candidate]]:
    max_size = CATEGORIES[category][1][-1]
    template = _load(f"group_{category}.txt")
    out: list[list[Candidate]] = []
    for start in range(0, len(pool), chunk):
        part = pool[start : start + chunk]
        statements = "\n".join(
            f"[{i}] ({c.speakers[0] if c.speakers else '?'}) {c.statement}"
            for i, c in enumerate(part)
        )
        reply = run.ask(
            _fill(template, max_size=str(max_size), statements=statements), json_reply=True
        )
        for entry in reply or []:
            run.stats.groups_proposed += 1
            idx = entry.get("indices") if isinstance(entry, dict) else None
            if (
                not isinstance(idx, list)
                or not idx
                or not all(
                    isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(part)
                    for i in idx
                )
                or len(set(idx)) > max_size
            ):
                run.stats.dropped_bad_group += 1
                continue
            out.append([part[i] for i in sorted(set(idx))])
    return out


def _changing_groups(run: _Run, candidates: list[Candidate]) -> list[list[Candidate]]:
    by_subject: dict[str, list[Candidate]] = {}
    for c in candidates:
        key = norm(c.subject or "")
        if c.kind == "changing_value" and key and norm(c.value or ""):
            by_subject.setdefault(key, []).append(c)
    out: list[list[Candidate]] = []
    for group in by_subject.values():
        stamps = [c.timestamps[0] if c.timestamps else None for c in group]
        try:
            if any(t is None for t in stamps):
                raise TypeError("missing timestamp")
            ordered = [c for _, c in sorted(zip(stamps, group, strict=True), key=lambda p: p[0])]  # type: ignore[arg-type,return-value]
        except TypeError:
            run.stats.dropped_chronology += 1
            continue
        merged: list[Candidate] = []
        for c in ordered:
            if merged and norm(merged[-1].value or "") == norm(c.value or ""):
                merged[-1] = c  # same value again is no change; keep the later message
            else:
                merged.append(c)
        if len(merged) >= 2:
            out.append(merged[-_MAX_CHANGING:])
    return out


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _has_value(answer: str, value: str) -> bool:
    """Does ``answer`` contain ``value``? Numbers compare as numbers ("$1,500" holds 1500); the
    rest of the value, and values without numbers, compare as normalized word sequences."""
    nums = numbers(value)
    if not nums:
        return f" {norm(value)} " in f" {norm(answer)} "
    rest = words(_NUMBER.sub(" ", value))
    return nums <= numbers(answer) and set(rest) <= set(words(answer))


def _wording(run: _Run, category: str, group: list[Candidate]) -> tuple[str, str] | None:
    prompt = _fill(_load(f"items_{category}.txt"), facts=_facts_text(group))
    reply = run.ask(prompt, json_reply=True)
    if reply is None:
        return None
    entry = reply[0] if reply else None
    q = entry.get("question") if isinstance(entry, dict) else None
    a = entry.get("answer") if isinstance(entry, dict) else None
    if not (isinstance(q, str) and q.strip() and isinstance(a, str) and a.strip()):
        run.stats.dropped_missing_fields += 1
        return None
    return q.strip(), a.strip()


def _in_filler(run: _Run, item: EvidenceItem) -> str | None:
    """convomem-arena ``check_filler``: the counter name to bump, or ``None`` if the item passes."""
    if item.category in _FACT_CATEGORIES:
        terms, minimum, counter = (
            specifics(item.answer, item.question),
            2,
            "dropped_answer_in_filler",
        )
    elif item.category == "abstention":
        terms, minimum, counter = content_words(item.question), 3, "dropped_topic_in_filler"
    else:
        return None
    if len(terms) < minimum:
        return None
    for _, ws, ns in run.filler(set(item.conversation_ids)):
        hits = [t for t in terms if (t in ns if is_number(t) else t in ws)]
        if counter == "dropped_answer_in_filler" and all(is_number(h) for h in hits):
            continue  # numbers alone recur by coincidence
        if len(hits) / len(terms) >= FILLER_THRESHOLD:
            return counter
    return None


def _judge_file(item: EvidenceItem) -> str:
    if item.category == "user_facts":
        return (
            "judge_user_facts_single.txt"
            if item.evidence_count == 1
            else "judge_user_facts_multi.txt"
        )
    if item.category == "abstention":
        return "judge_abstention.txt"
    return "judge_rubric.txt" if item.category in _RUBRIC_CATEGORIES else "judge_default.txt"


def _verified(run: _Run, item: EvidenceItem) -> bool:
    wanted = set(item.conversation_ids)
    blocks = []
    for n, cid in enumerate((c for c in run.order if c in wanted), 1):
        lines = "\n".join(f"{m['speaker']}: {m['text']}" for m in run.messages[cid])
        blocks.append(f"Conversation {n}:\n{lines}")
    answered = run.ask(
        _fill(
            _load("verify_answer.txt"), conversations="\n\n".join(blocks), question=item.question
        ),
        json_reply=False,
    )
    if not answered or not isinstance(answered[0], str):
        return False
    if item.evidence_count == 1:
        evidence = item.evidence[0].text
    else:
        evidence = "\n".join(
            f"Evidence Message {i}: {e.text}" for i, e in enumerate(item.evidence, 1)
        )
    verdict = run.ask(
        _fill(
            _load(_judge_file(item)),
            question=item.question,
            answer=item.answer,
            response=answered[0],
            evidence=evidence,
        ),
        json_reply=False,
    )
    match = _VERDICT.match(verdict[0]) if verdict and isinstance(verdict[0], str) else None
    return bool(match and match.group(1).upper() == "RIGHT")


def build_items(
    candidates: list[Candidate],
    sessions: list[Session],
    client: ChatClient,
    *,
    verify: bool = True,
    cache_dir: str | os.PathLike[str] | None = None,
    group_chunk: int = 50,
    stats: Stats | None = None,
) -> tuple[list[EvidenceItem], Stats]:
    """Build evidence items for all six categories from ``candidates``.

    ``sessions`` must be the redacted sessions the candidates came from (see the module
    docstring). Returns the items, in category order, and the :class:`Stats` (pass ``stats`` to
    reuse one). ``group_chunk >= 1`` is the number of candidates per grouping call. A failed model
    call or unusable reply only loses that group or item and is counted, never raised.
    """
    if group_chunk < 1:
        raise ValueError("group_chunk must be >= 1")
    stats = stats if stats is not None else Stats()
    run = _Run(client, sessions, cache_dir, stats)
    items: list[EvidenceItem] = []
    seen_questions: set[str] = set()
    for category, (_, counts) in CATEGORIES.items():
        if category == "changing_facts":
            groups = _changing_groups(run, candidates)
        else:
            pool = [c for c in candidates if c.kind in _POOLS[category]]
            groups = _model_groups(run, category, pool, group_chunk) if pool else []
        for group in groups:
            evidence = _evidence_of(group)
            if len(evidence) not in counts:
                stats.dropped_bad_group += 1
                continue
            if len({e.conversation_id for e in evidence}) != len(evidence):
                stats.dropped_shared_conversation += 1  # ConvoMem: one conversation per evidence
                continue
            if not all(run.verbatim(e) for e in evidence):
                stats.dropped_evidence_not_verbatim += 1
                continue
            worded = _wording(run, category, group)
            if worded is None:
                continue
            question, answer = worded
            if category == "changing_facts" and not _has_value(answer, group[-1].value or ""):
                stats.dropped_answer_mismatch += 1
                continue
            key = norm(question)
            if key in seen_questions:
                stats.dropped_duplicate_question += 1
                continue
            cids = list(dict.fromkeys(e.conversation_id for e in evidence))
            item = EvidenceItem(category, len(evidence), question, answer, evidence, cids)
            counter = _in_filler(run, item)
            if counter:
                setattr(stats, counter, getattr(stats, counter) + 1)
                continue
            if verify and not _verified(run, item):
                stats.dropped_verification += 1
                continue
            seen_questions.add(key)
            items.append(item)
            stats.items += 1
            stats.items_by_category[category] += 1
    log.info("build_items: %s", json.dumps(stats.to_dict()))
    return items, stats
