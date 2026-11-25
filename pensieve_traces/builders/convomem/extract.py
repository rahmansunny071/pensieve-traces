"""Find candidate memory facts in conversations with a language model.

Input is any list of canonical sessions (Claude Code, claude.ai, ...), turned into conversations
by :func:`~pensieve_traces.writers.convomem.sessions_to_conversations_with_ids` with its default
``tool_activity``, so candidate texts equal the messages a ConvoMem filler file will hold.

PRIVACY: this module does not redact. Everything in ``sessions`` is sent to the endpoint behind
``client``. The caller must pass sessions that already went through
:func:`~pensieve_traces.prepare_for_output` (one anonymizer for all inputs).

Prompts are adapted from Salesforce ConvoMem (Apache-2.0); see ``prompts/`` and the repo
``NOTICE``. The model is asked to return JSON; code, not the model, checks every index and copies
the message text verbatim. Invalid candidates are dropped and counted, never repaired.

Each conversation is cut into windows of ``window`` messages, ``window - overlap`` apart, the last
one reaching the end. The model sees window-local indices ``[0] .. [n-1]``; :class:`Candidate`
stores the conversation-global ``positions`` as well as the message ids.

Replies are cached per window (see :func:`extract_candidates`). A window whose model call fails is
skipped and counted in ``ExtractStats.failed_windows``; one bad window never aborts the run.
``dropped_bad_json`` counts windows whose reply was unparseable. So a caller (#55) must treat
``failed_windows + dropped_bad_json == windows`` (nothing usable came back) as an error.
Ollama's small default context can silently truncate oversized prompts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from pensieve_traces.builders.llm import ChatClient, ModelClientError
from pensieve_traces.model import Session
from pensieve_traces.writers.convomem import sessions_to_conversations_with_ids

__all__ = [
    "CACHE_FORMAT_VERSION",
    "KINDS",
    "PROMPT_DELIMITER",
    "PROMPT_FILES",
    "Candidate",
    "ExtractStats",
    "extract_candidates",
    "load_prompt",
    "prompt_hashes",
]

log = logging.getLogger(__name__)

CACHE_FORMAT_VERSION = 1
Kind = Literal["user_fact", "assistant_fact", "preference", "changing_value"]
KINDS: tuple[str, ...] = ("user_fact", "assistant_fact", "preference", "changing_value")
# Each prompt file is an attribution header, this line, then the prompt body.
PROMPT_DELIMITER = "=====PROMPT====="
PROMPT_FILES = ("extract_system.txt", "extract_user.txt")
_PROMPTS = resources.files("pensieve_traces.builders.convomem") / "prompts"
_CACHE_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
_THINK = re.compile(r"\A\s*<think>.*?</think>", re.DOTALL)
_FENCE = re.compile(r"\A\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n?\s*```\s*\Z", re.DOTALL)


@dataclass
class Candidate:
    """A fact found in a conversation, with the exact messages it comes from."""

    kind: Kind
    statement: str
    conversation_id: str
    message_ids: list[str]
    speakers: list[str]
    texts: list[str]  # full messages, verbatim, index-aligned with message_ids
    timestamps: list[datetime | None]
    positions: list[int] = field(default_factory=list)  # message indices in the conversation
    subject: str | None = None  # changing_value only
    value: str | None = None  # changing_value only

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "statement": self.statement,
            "conversation_id": self.conversation_id,
            "message_ids": list(self.message_ids),
            "speakers": list(self.speakers),
            "texts": list(self.texts),
            "timestamps": [None if t is None else t.isoformat() for t in self.timestamps],
            "positions": list(self.positions),
            "subject": self.subject,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        return cls(
            kind=data["kind"],
            statement=data["statement"],
            conversation_id=data["conversation_id"],
            message_ids=list(data["message_ids"]),
            speakers=list(data["speakers"]),
            texts=list(data["texts"]),
            timestamps=[
                None if t is None else datetime.fromisoformat(t) for t in data["timestamps"]
            ],
            positions=list(data.get("positions", [])),
            subject=data.get("subject"),
            value=data.get("value"),
        )


@dataclass
class ExtractStats:
    windows: int = 0
    model_calls: int = 0
    cache_hits: int = 0
    failed_windows: int = 0
    candidates: int = 0
    merged_duplicates: int = 0
    dropped_bad_json: int = 0
    dropped_unknown_kind: int = 0
    dropped_bad_index: int = 0
    dropped_missing_fields: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def load_prompt(name: str) -> str:
    """Return the prompt body of ``prompts/<name>`` (attribution header stripped)."""
    text = (_PROMPTS / name).read_text(encoding="utf-8")
    _, sep, body = text.partition(PROMPT_DELIMITER + "\n")
    if not sep:
        raise ValueError(f"prompt {name} has no {PROMPT_DELIMITER} line")
    return body


def prompt_hashes() -> dict[str, str]:
    """SHA-256 of each prompt file (whole file), keyed by file name, for build manifests."""
    return {n: hashlib.sha256((_PROMPTS / n).read_bytes()).hexdigest() for n in PROMPT_FILES}


def _parse_reply(reply: str) -> list[Any]:
    """Tolerant parse: drop a leading <think> block and one code fence, accept a list or a
    one-list-value object. Raises ``ValueError``/``TypeError`` for anything else."""
    text = _THINK.sub("", reply, count=1).strip()
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    if isinstance(data, dict):
        if len(data) != 1 or not isinstance(next(iter(data.values())), list):
            raise TypeError("reply object is not a single list")
        data = next(iter(data.values()))
    if not isinstance(data, list):
        raise TypeError("reply is not a list")
    return data


def _render(window: list[dict[str, str]]) -> list[dict[str, str]]:
    body = "\n".join(f"[{i}] {m['speaker']}: {m['text']}" for i, m in enumerate(window))
    return [
        {"role": "system", "content": load_prompt("extract_system.txt")},
        {"role": "user", "content": load_prompt("extract_user.txt").replace("{messages}", body)},
    ]


def _cache_path(cache_dir: Path, messages: list[dict[str, str]], model: str) -> Path:
    blob = json.dumps(
        {"v": CACHE_FORMAT_VERSION, "model": model, "messages": messages}, sort_keys=True
    )
    return cache_dir / f"{hashlib.sha256(blob.encode()).hexdigest()}.json"


def _cache_read(path: Path) -> list[Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("version") == CACHE_FORMAT_VERSION:
        reply = data.get("reply")
        if isinstance(reply, list):
            return reply
    return None


def _cache_write(path: Path, reply: list[Any]) -> None:
    assert _CACHE_NAME.match(path.name)
    payload = json.dumps({"version": CACHE_FORMAT_VERSION, "reply": reply})
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validate(
    item: Any, size: int, stats: ExtractStats
) -> tuple[str, str, list[int], str | None, str | None] | None:
    if not isinstance(item, dict):
        stats.dropped_missing_fields += 1
        return None
    kind = item.get("kind")
    if kind not in KINDS:
        stats.dropped_unknown_kind += 1
        return None
    statement = item.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        stats.dropped_missing_fields += 1
        return None
    subject, value = item.get("subject"), item.get("value")
    if kind == "changing_value":
        if not all(isinstance(v, str) and v.strip() for v in (subject, value)):
            stats.dropped_missing_fields += 1
            return None
    else:
        subject = value = None
    indices = item.get("indices")
    if (
        not isinstance(indices, list)
        or not indices
        or not all(
            isinstance(i, int) and not isinstance(i, bool) and 0 <= i < size for i in indices
        )
    ):
        stats.dropped_bad_index += 1
        return None
    return kind, statement.strip(), sorted(set(indices)), subject, value


def extract_candidates(
    sessions: list[Session],
    client: ChatClient,
    *,
    window: int = 40,
    overlap: int = 10,
    cache_dir: str | os.PathLike[str] | None = None,
    stats: ExtractStats | None = None,
) -> list[Candidate]:
    """Ask ``client`` for candidate facts in every conversation of ``sessions``.

    ``sessions`` must already be redacted (see the module docstring). Needs
    ``window >= 1`` and ``0 <= overlap < window``. Pass ``stats=ExtractStats()`` to read the
    counts. Duplicates from overlapping windows (same conversation, kind and messages) are merged,
    keeping the first statement.

    Cache: with ``cache_dir``, each window's parsed reply is stored as ``<64-hex>.json``, keyed on
    the exact request messages, ``client.model`` and ``CACHE_FORMAT_VERSION``; replies are
    validated again on every load. The directory is created if missing
    (``NotADirectoryError`` if the path is a file, before any model call). Only those files are
    created or replaced (atomically); nothing else is listed or deleted. A corrupt entry is a
    miss and is replaced. Windows whose call or parse failed are not cached.
    """
    if window < 1 or not 0 <= overlap < window:
        raise ValueError("need window >= 1 and 0 <= overlap < window")
    stats = stats if stats is not None else ExtractStats()
    cache: Path | None = None
    if cache_dir is not None:
        cache = Path(cache_dir)
        if cache.exists() and not cache.is_dir():
            raise NotADirectoryError(f"cache path is not a directory: {cache}")
        cache.mkdir(parents=True, exist_ok=True)

    conversations, id_map = sessions_to_conversations_with_ids(sessions)
    by_session = {s.id: s for s in sessions}
    step = window - overlap
    found: dict[tuple[str, str, tuple[str, ...]], Candidate] = {}
    for conv in conversations:
        cid = conv["id"]
        msgs = conv["messages"]
        ids = id_map[cid]
        session = by_session[cid]
        starts = list(range(0, max(len(msgs) - overlap, 1), step))
        for start in starts:
            chunk = msgs[start : start + window]
            stats.windows += 1
            request = _render(chunk)
            path = _cache_path(cache, request, client.model) if cache else None
            reply = _cache_read(path) if path else None
            if reply is not None:
                stats.cache_hits += 1
            else:
                stats.model_calls += 1
                try:
                    raw = client.chat(request)
                except ModelClientError:
                    stats.failed_windows += 1
                    continue
                try:
                    reply = _parse_reply(raw)
                except (ValueError, TypeError):
                    stats.dropped_bad_json += 1
                    continue
                if path:
                    _cache_write(path, reply)
            for item in reply:
                ok = _validate(item, len(chunk), stats)
                if ok is None:
                    continue
                kind, statement, local, subject, value = ok
                pos = [start + i for i in local]
                mids = [ids[p] for p in pos]
                key = (cid, kind, tuple(mids))
                if key in found:
                    stats.merged_duplicates += 1
                    continue
                found[key] = Candidate(
                    kind=kind,  # type: ignore[arg-type]
                    statement=statement,
                    conversation_id=cid,
                    message_ids=mids,
                    speakers=[msgs[p]["speaker"] for p in pos],
                    texts=[msgs[p]["text"] for p in pos],
                    timestamps=[
                        (m.timestamp if (m := session.message(i)) is not None else None)
                        for i in mids
                    ],
                    positions=pos,
                    subject=subject,
                    value=value,
                )
    stats.candidates = len(found)
    log.info("extract_candidates: %s", json.dumps(stats.to_dict()))
    return list(found.values())
