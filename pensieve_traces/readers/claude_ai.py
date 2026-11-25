"""claude.ai export reader: ``conversations.json`` to canonical :class:`~pensieve_traces.Session`.

Reads the ``conversations.json`` of a claude.ai data export (Settings, Privacy, Export data),
one session per conversation, returned in file order. Nothing else in the export is opened:
``account``, ``summary``, ``memories/``, ``projects/``, ``artifacts/``, ``light_metadata-*`` and
the ``manifest-*.json`` (signed URLs) are never read. No redaction is done here: run the
redaction pass before any writer. Note that ``injected_prompt_block`` text (system notes such as
the memory header) is kept, as ``is_meta`` messages.

Message text comes from ``content`` blocks, never the top-level ``text`` (it disagrees with the
blocks in practice).

Human messages
    Each ``injected_prompt_block`` is its own ``is_meta=True`` user message. ``text``,
    ``document`` (``[attached: <title>]``), ``image`` (``[image]``) and unknown blocks merge, in
    block order, into one non-meta user message, which also receives placeholders for
    ``files[]`` and ``attachments[]`` (``[attached: <file_name>]``). ``include_attachments=True``
    appends an attachment's ``extracted_content`` after its placeholder; by default it is dropped.

Assistant messages
    Consecutive ``thinking`` / ``text`` / ``tool_use`` blocks form one assistant message
    (``reasoning``, ``text``, ``tool_calls``); each ``tool_result`` is a ``Role.TOOL`` message
    that ends the run, and later blocks start a new assistant message. Non-text tool result
    elements are flattened to short text (``[web results: ...]``, ``[images: N]``,
    ``[local resource: ...]``), never raw JSON. Unknown blocks, and blocks on the wrong sender,
    become ``[unknown block: <type>]``.

Id and parent contract (same as the Claude Code reader)
    A source message that yields several canonical messages emits them in order, chained. The
    last one gets the source ``uuid``; earlier ones get ``uuid#0``, ``uuid#1``, ... (position
    among the derived messages). The first is parented to the source's parent and each later one
    to the previous, so the next source message, whose ``parent_message_uuid`` is the source
    ``uuid``, is parented to the last derived message. The root sentinel parent, and a parent
    missing from the file, give ``None``. A source message that yields nothing is skipped and its
    child links to the nearest kept ancestor. Derived messages share the source ``created_at``.

Branches
    The export has no current-leaf pointer. Only one path is kept: the leaf with the latest
    ``created_at`` (ties: the later one in the file) and its ancestors, which also picks the
    root. ``Session.metadata["dropped_branch_messages"]`` counts the dropped source messages.

Errors
    :class:`ClaudeAiParseError` (a ``ValueError`` with ``path`` and ``location``) is raised for
    bad JSON or UTF-8, a non-array file, wrong structure, missing required keys, an unknown
    ``sender``, bad or timezone-naive timestamps, repeated uuids, parent cycles and id collisions.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pensieve_traces.model import Message, Role, Session, ToolCall

__all__ = ["HARNESS", "ClaudeAiParseError", "read_export"]

HARNESS = "claude.ai"
_ROOT_PARENT = "00000000-0000-4000-8000-000000000000"
_FILE = "conversations.json"


class ClaudeAiParseError(ValueError):
    """An export could not be parsed; carries the file ``path`` and a ``location`` string."""

    def __init__(self, path: str | os.PathLike[str], location: str, reason: str) -> None:
        self.path = os.fspath(path)
        self.location = location
        self.reason = reason
        super().__init__(f"{self.path}: {location}: {reason}")


@dataclass
class _Src:
    uuid: str
    parent: str | None
    created: datetime
    index: int
    raw: dict[str, Any]


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class _Reader:
    def __init__(self, path: Path, include_attachments: bool) -> None:
        self.path = path
        self.include_attachments = include_attachments

    def fail(self, location: str, reason: str) -> ClaudeAiParseError:
        return ClaudeAiParseError(self.path, location, reason)

    def timestamp(self, value: Any, location: str, *, required: bool) -> datetime | None:
        if value is None:
            if required:
                raise self.fail(location, "missing timestamp")
            return None
        if not isinstance(value, str):
            raise self.fail(location, "timestamp is not a string")
        try:
            ts = datetime.fromisoformat(value)
        except ValueError as exc:
            raise self.fail(location, f"bad timestamp {value!r}") from exc
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise self.fail(location, f"timestamp {value!r} is not timezone-aware")
        return ts

    def load(self) -> list[Session]:
        try:
            raw = self.path.read_bytes().decode("utf-8")
            data = json.loads(raw)
        except UnicodeDecodeError as exc:
            raise self.fail("file", f"invalid UTF-8: {exc.reason}") from exc
        except (ValueError, RecursionError) as exc:
            raise self.fail("file", f"invalid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise self.fail("file", "top level is not a JSON array")
        sessions: list[Session] = []
        seen: set[str] = set()
        for i, conv in enumerate(data):
            loc = f"conversations[{i}]"
            if not isinstance(conv, dict):
                raise self.fail(loc, "conversation is not an object")
            session = self.conversation(conv, loc)
            if session.id in seen:
                raise self.fail(loc, f"repeated conversation uuid {session.id!r}")
            seen.add(session.id)
            sessions.append(session)
        return sessions

    def conversation(self, conv: dict[str, Any], loc: str) -> Session:
        uuid = _str(conv.get("uuid"))
        if uuid is None:
            raise self.fail(loc, "conversation has no string uuid")
        msgs = conv.get("chat_messages")
        if not isinstance(msgs, list):
            raise self.fail(loc, "chat_messages is not an array")
        name = conv.get("name")
        if name is not None and not isinstance(name, str):
            raise self.fail(loc, "name is not a string")
        started = self.timestamp(conv.get("created_at"), f"{loc}.created_at", required=False)
        ended = self.timestamp(conv.get("updated_at"), f"{loc}.updated_at", required=False)

        srcs: list[_Src] = []
        by_uuid: dict[str, _Src] = {}
        for j, raw in enumerate(msgs):
            mloc = f"{loc}.chat_messages[{j}]"
            if not isinstance(raw, dict):
                raise self.fail(mloc, "message is not an object")
            muuid = _str(raw.get("uuid"))
            if muuid is None:
                raise self.fail(mloc, "message has no string uuid")
            if muuid in by_uuid:
                raise self.fail(mloc, f"repeated message uuid {muuid!r}")
            if raw.get("sender") not in (
                "human",
                "assistant",
            ):  # every branch, not only the kept one
                raise self.fail(mloc, f"unknown sender {raw.get('sender')!r}")
            parent = raw.get("parent_message_uuid")
            if parent is not None and not isinstance(parent, str):
                raise self.fail(mloc, "parent_message_uuid is not a string")
            if parent in (_ROOT_PARENT, ""):
                parent = None
            created = self.timestamp(raw.get("created_at"), f"{mloc}.created_at", required=True)
            assert created is not None
            src = _Src(muuid, parent, created, j, raw)
            srcs.append(src)
            by_uuid[muuid] = src
        for src in srcs:  # a parent absent from the file makes a root
            if src.parent is not None and src.parent not in by_uuid:
                src.parent = None
        self.check_cycles(srcs, by_uuid, loc)

        path = self.select_path(srcs, by_uuid)
        messages: list[Message] = []
        used: set[str] = set()
        last_of: dict[str, str | None] = {}  # source uuid -> last canonical id (nearest kept)
        for src in path:
            mloc = f"{loc}.chat_messages[{src.index}]"
            parent_id = None if src.parent is None else last_of.get(src.parent)
            derived = self.derive(src, mloc)
            prev = parent_id
            for k, kwargs in enumerate(derived):
                mid = src.uuid if k == len(derived) - 1 else f"{src.uuid}#{k}"
                if mid in used:
                    raise self.fail(mloc, f"message id {mid!r} collides with an earlier message")
                used.add(mid)
                messages.append(Message(id=mid, parent_id=prev, timestamp=src.created, **kwargs))
                prev = mid
            last_of[src.uuid] = prev
        return Session(
            id=uuid,
            harness=HARNESS,
            title=name or None,
            started_at=started,
            ended_at=ended,
            metadata={"dropped_branch_messages": len(srcs) - len(path)},
            messages=messages,
        )

    def check_cycles(self, srcs: list[_Src], by_uuid: dict[str, _Src], loc: str) -> None:
        done: set[str] = set()
        for src in srcs:
            chain: set[str] = set()
            cur: _Src | None = src
            while cur is not None and cur.uuid not in done:
                if cur.uuid in chain:
                    raise self.fail(
                        f"{loc}.chat_messages[{cur.index}]", "parent links form a cycle"
                    )
                chain.add(cur.uuid)
                cur = None if cur.parent is None else by_uuid[cur.parent]
            done |= chain

    def select_path(self, srcs: list[_Src], by_uuid: dict[str, _Src]) -> list[_Src]:
        if not srcs:
            return []
        parents = {s.parent for s in srcs if s.parent is not None}
        leaves = [s for s in srcs if s.uuid not in parents]
        leaf = max(leaves, key=lambda s: (s.created, s.index))
        path: list[_Src] = []
        cur: _Src | None = leaf
        while cur is not None:
            path.append(cur)
            cur = None if cur.parent is None else by_uuid[cur.parent]
        path.reverse()
        return path

    # -- block handling --------------------------------------------------

    def derive(self, src: _Src, loc: str) -> list[dict[str, Any]]:
        sender = src.raw.get("sender")
        if sender not in ("human", "assistant"):
            raise self.fail(loc, f"unknown sender {sender!r}")
        content = src.raw.get("content")
        if content is None:
            content = []
        if not isinstance(content, list):
            raise self.fail(loc, "content is not an array")
        for block in content:
            if not isinstance(block, dict):
                raise self.fail(loc, "content block is not an object")
        if sender == "human":
            return self.human(src.raw, content, loc)
        return self.assistant(content, loc)

    def human(self, raw: dict[str, Any], blocks: list[Any], loc: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        user_parts: list[str] = []
        user_slot: dict[str, Any] | None = None

        def add_user(part: str) -> None:
            nonlocal user_slot
            if user_slot is None:
                user_slot = {"role": Role.USER, "text": ""}
                out.append(user_slot)
            user_parts.append(part)

        for block in blocks:
            btype = block.get("type")
            if btype == "injected_prompt_block":
                prompt = block.get("prompt")
                if isinstance(prompt, str) and prompt:
                    out.append({"role": Role.USER, "text": prompt, "is_meta": True})
            elif btype == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    add_user(text)
            elif btype == "document":
                add_user(f"[attached: {_str(block.get('title')) or 'document'}]")
            elif btype == "image":
                add_user("[image]")
            else:
                add_user(f"[unknown block: {btype if isinstance(btype, str) else '?'}]")
        for f in self.list_of(raw, "files", loc):
            add_user(f"[attached: {_str(f.get('file_name')) or 'file'}]")
        for a in self.list_of(raw, "attachments", loc):
            add_user(f"[attached: {_str(a.get('file_name')) or 'attachment'}]")
            extracted = a.get("extracted_content")
            if self.include_attachments and isinstance(extracted, str) and extracted:
                add_user(extracted)
        if user_slot is not None:
            user_slot["text"] = "\n\n".join(user_parts)
        return out

    def list_of(self, raw: dict[str, Any], key: str, loc: str) -> list[dict[str, Any]]:
        value = raw.get(key)
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise self.fail(loc, f"{key} is not an array of objects")
        return value

    def assistant(self, blocks: list[Any], loc: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        reasoning: list[str] = []
        texts: list[str] = []
        calls: list[ToolCall] = []

        def flush() -> None:
            if reasoning or texts or calls:
                out.append(
                    {
                        "role": Role.ASSISTANT,
                        "text": "\n\n".join(texts),
                        "reasoning": "\n\n".join(reasoning) if reasoning else None,
                        "tool_calls": list(calls),
                    }
                )
            reasoning.clear()
            texts.clear()
            calls.clear()

        for block in blocks:
            btype = block.get("type")
            if btype == "thinking":
                thinking = block.get("thinking")
                if isinstance(thinking, str) and thinking:
                    reasoning.append(thinking)
            elif btype == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
            elif btype == "tool_use":
                cid, name = _str(block.get("id")), _str(block.get("name"))
                if cid is None or name is None:
                    raise self.fail(loc, "tool_use has no string id or name")
                args = json.dumps(block["input"]) if "input" in block else "{}"
                calls.append(ToolCall(id=cid, name=name, arguments=args))
            elif btype == "tool_result":
                call_id = _str(block.get("tool_use_id"))
                if call_id is None:
                    raise self.fail(loc, "tool_result has no string tool_use_id")
                flush()
                out.append(
                    {
                        "role": Role.TOOL,
                        "text": self.flatten(block.get("content")),
                        "tool_call_id": call_id,
                        "is_error": block.get("is_error") is True,
                    }
                )
            else:
                texts.append(f"[unknown block: {btype if isinstance(btype, str) else '?'}]")
        flush()
        return out

    @staticmethod
    def flatten(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        titles: list[str] = []
        for el in content:
            if not isinstance(el, dict):
                continue
            etype = el.get("type")
            if etype == "text":
                text = el.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif etype == "knowledge":
                if not titles:
                    parts.append("")  # placeholder, filled with every title below
                titles.append(_str(el.get("title")) or "untitled")
            elif etype == "image_gallery":
                images = el.get("images")
                n = len(images) if isinstance(images, list) else 0
                parts.append(f"[images: {n}]")
            elif etype == "local_resource":
                name = _str(el.get("name")) or _str(el.get("title")) or "unnamed"
                parts.append(f"[local resource: {name}]")
            else:
                parts.append(f"[unknown content: {etype if isinstance(etype, str) else '?'}]")
        if titles:
            parts[parts.index("")] = "[web results: " + ", ".join(titles) + "]"
        return "\n".join(parts)


def read_export(
    path: str | os.PathLike[str], *, include_attachments: bool = False
) -> list[Session]:
    """Read a claude.ai export (directory or ``conversations.json``) into sessions, file order.

    Attachment text (``extracted_content``) is dropped unless ``include_attachments`` is true.
    Does not redact.
    """
    p = Path(path)
    if p.is_dir():
        p = p / _FILE
    return _Reader(p, include_attachments).load()
