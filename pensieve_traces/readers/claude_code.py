"""Claude Code reader: raw session JSONL to the canonical :class:`~pensieve_traces.Session`.

Only the conversation is kept. Records are filtered with an allowlist: ``user``,
``assistant`` and ``system`` records become messages, an ``ai-title`` record sets
``Session.title`` (from its top-level string ``aiTitle``), and every other record type
(file backups, API request blobs, cost state, ...) is skipped with a debug log. Kept records
are read only through the fields listed here; top-level ``toolUseResult`` is ignored and
dropped-record content is never stored anywhere, including ``metadata``. No redaction is done
here: run the redaction pass before any writer.

Id and parent contract
    A record that yields several messages emits them in block order, chained. The last one
    gets the record's ``uuid``; earlier ones get ``uuid#0``, ``uuid#1``, ... Each message is
    parented to the previous one and the first to the record's ``parentUuid``. A record
    that yields no blocks yields no message. Repeated record uuids are skipped (debug log).

Usage contract
    Assistant records sharing a ``requestId`` stay separate messages. For each ``requestId``,
    the usage of the last surviving assistant record is put on the last kept assistant message
    of that group in the final merged order; every other message in the group has no usage.
    Surviving records are those left after de-duplication by record ``uuid`` (a ``uuid`` seen
    in an earlier file of the session is skipped, for usage exactly as for messages); records
    that produce no message still count. "Last" uses the same stable order as the messages:
    timestamp (untimestamped last), then file order, then position in the file. This also
    holds when the group spans several files of one session. If the group produced no
    messages, its usage is dropped (debug log). A message without ``requestId`` keeps its own
    record's usage.
    Summing usage across messages gives the per-request total. Key mapping:
    ``input_tokens`` -> ``input_tokens``, ``output_tokens`` -> ``output_tokens``,
    ``cache_read_input_tokens`` -> ``cache_read_tokens``,
    ``cache_creation_input_tokens`` -> ``cache_creation_tokens``. Missing or null counts are
    0 and other keys are ignored.

Errors
    :class:`ClaudeCodeParseError` (a ``ValueError`` with ``path`` and ``line``) is raised for
    bad JSON, non-object lines, and kept records with the wrong structure, including bad or
    timezone-naive timestamps and malformed ``usage``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pensieve_traces.model import Message, Role, Session, ToolCall, Usage

__all__ = ["ClaudeCodeParseError", "read_project", "read_session"]

logger = logging.getLogger(__name__)

HARNESS = "claude_code"
_KEPT_TYPES = frozenset({"user", "assistant", "system"})
_USAGE_KEYS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
    "cache_creation_input_tokens": "cache_creation_tokens",
}


class ClaudeCodeParseError(ValueError):
    """A session file could not be parsed; carries the file ``path`` and 1-based ``line``."""

    def __init__(self, path: str | os.PathLike[str], line: int, reason: str) -> None:
        self.path = os.fspath(path)
        self.line = line
        self.reason = reason
        super().__init__(f"{self.path}:{line}: {reason}")


@dataclass
class _UsageRecord:
    uuid: str
    timestamp: datetime | None
    request_id: str
    usage: Usage | None


@dataclass
class _ParsedFile:
    path: Path
    session_id: str
    cwd: str | None = None
    git_branch: str | None = None
    title: str | None = None
    messages: list[Message] = field(default_factory=list)
    record_uuids: list[str] = field(default_factory=list)  # parallel to messages
    kept_uuids: set[str] = field(default_factory=set)  # every kept record uuid
    # Assistant records with a requestId, in file order: (uuid, timestamp, request_id, usage).
    usage_records: list[_UsageRecord] = field(default_factory=list)


def _join(parts: list[str]) -> str:
    return "\n\n".join(parts)


class _FileParser:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.line = 0
        self.parsed = _ParsedFile(path=path, session_id=path.stem)
        self._session_id: str | None = None
        self._seen_uuids: set[str] = set()
        self._seen_ids: set[str] = set()
        self._pending: list[tuple[Message, Usage | None]] = []
        self._record_uuids: list[str] = []
        self._usage_records: list[_UsageRecord] = []

    def fail(self, reason: str) -> ClaudeCodeParseError:
        return ClaudeCodeParseError(self.path, self.line, reason)

    def parse(self) -> _ParsedFile:
        with open(self.path, "rb") as fh:
            for self.line, raw in enumerate(fh, start=1):
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise self.fail(f"invalid UTF-8: {exc.reason}") from exc
                if text.strip():
                    self._record(text)
        for msg, usage in self._pending:
            if msg.request_id is None:
                msg.usage = usage  # no requestId: keeps its own record's usage
        self.parsed.usage_records = self._usage_records
        self.parsed.kept_uuids = self._seen_uuids
        if self._session_id is not None:
            self.parsed.session_id = self._session_id
        self.parsed.messages = [m for m, _ in self._pending]
        self.parsed.record_uuids = self._record_uuids
        return self.parsed

    # -- records ---------------------------------------------------------

    def _record(self, raw: str) -> None:
        try:
            rec = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise self.fail(f"invalid JSON: {exc}") from exc
        if not isinstance(rec, dict):
            raise self.fail("line is not a JSON object")
        rtype = rec.get("type")
        if rtype == "ai-title":
            title = rec.get("aiTitle")
            if isinstance(title, str):
                self.parsed.title = title
            else:
                logger.debug(
                    "%s:%d: ignoring ai-title without a string aiTitle", self.path, self.line
                )
            return
        if not isinstance(rtype, str) or rtype not in _KEPT_TYPES:
            logger.debug("%s:%d: skipping record of type %r", self.path, self.line, rtype)
            return
        self._kept(rec, rtype)

    def _kept(self, rec: dict[str, Any], rtype: str) -> None:
        uuid = rec.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise self.fail(f"{rtype} record has no string uuid")
        parent = rec.get("parentUuid")
        if parent is not None and not isinstance(parent, str):
            raise self.fail("parentUuid is not a string")
        timestamp = self._timestamp(rec.get("timestamp"))

        if self._session_id is None and _nonempty_str(rec.get("sessionId")):
            self._session_id = rec["sessionId"]
        if self.parsed.cwd is None and _nonempty_str(rec.get("cwd")):
            self.parsed.cwd = rec["cwd"]
        if self.parsed.git_branch is None and _nonempty_str(rec.get("gitBranch")):
            self.parsed.git_branch = rec["gitBranch"]

        if uuid in self._seen_uuids:
            logger.debug("%s:%d: skipping repeated uuid %r", self.path, self.line, uuid)
            return
        self._seen_uuids.add(uuid)

        usage: Usage | None = None
        model: str | None = None
        if rtype == "system":
            parts = self._system_parts(rec)
        else:
            message = rec.get("message")
            if not isinstance(message, dict):
                raise self.fail(f"{rtype} record has no message object")
            blocks = self._blocks(message.get("content"))
            if rtype == "user":
                parts = self._user_parts(blocks)
            else:
                parts = self._assistant_parts(blocks)
                usage = self._usage(message.get("usage"))
                model = message["model"] if isinstance(message.get("model"), str) else None
        request_id = rec.get("requestId")
        if rtype == "assistant" and isinstance(request_id, str):
            # Usage belongs to the record, even if it yields no message; resolved on merge.
            self._usage_records.append(_UsageRecord(uuid, timestamp, request_id, usage))
            usage = None
        if not parts:
            return

        common: dict[str, Any] = {
            "timestamp": timestamp,
            "model": model,
            "request_id": request_id if isinstance(request_id, str) else None,
            "is_sidechain": rec.get("isSidechain") is True,
            "is_meta": rec.get("isMeta") is True,
        }
        prev: str | None = parent
        for i, kwargs in enumerate(parts):
            mid = uuid if i == len(parts) - 1 else f"{uuid}#{i}"
            if mid in self._seen_ids:
                raise self.fail(f"message id {mid!r} collides with an earlier message")
            self._seen_ids.add(mid)
            msg = Message(id=mid, parent_id=prev, **common, **kwargs)
            self._pending.append((msg, usage if msg.role is Role.ASSISTANT else None))
            self._record_uuids.append(uuid)
            prev = mid

    # -- field helpers ---------------------------------------------------

    def _timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise self.fail("timestamp is not a string")
        try:
            ts = datetime.fromisoformat(value)
        except ValueError as exc:
            raise self.fail(f"bad timestamp {value!r}") from exc
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise self.fail(f"timestamp {value!r} is not timezone-aware")
        return ts

    def _blocks(self, content: Any) -> list[dict[str, Any]]:
        if content is None:
            return []
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if not isinstance(content, list):
            raise self.fail("message.content is neither a string nor a list")
        for block in content:
            if not isinstance(block, dict):
                raise self.fail("content block is not an object")
        return content

    def _usage(self, value: Any) -> Usage | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise self.fail("message.usage is not an object")
        counts: dict[str, int] = {}
        for src, dst in _USAGE_KEYS.items():
            n = value.get(src)
            if n is None:
                n = 0
            if isinstance(n, bool) or not isinstance(n, int):
                raise self.fail(f"usage.{src} is not an integer")
            counts[dst] = n
        return Usage(**counts)

    def _system_parts(self, rec: dict[str, Any]) -> list[dict[str, Any]]:
        text = rec.get("content")
        if not isinstance(text, str) or not text:
            return []
        subtype = rec.get("subtype")
        metadata = {"subtype": subtype} if isinstance(subtype, str) else {}
        return [{"role": Role.SYSTEM, "text": text, "metadata": metadata}]

    def _user_parts(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        texts: list[str] = []
        user_part: dict[str, Any] | None = None
        for block in blocks:
            btype = block.get("type")
            if btype in ("text", "image"):
                piece = block.get("text") if btype == "text" else "[image]"
                if isinstance(piece, str) and piece:
                    texts.append(piece)
                    if user_part is None:
                        user_part = {"role": Role.USER}
                        parts.append(user_part)
            elif btype == "tool_result":
                call_id = block.get("tool_use_id")
                if not isinstance(call_id, str):
                    raise self.fail("tool_result has no string tool_use_id")
                parts.append(
                    {
                        "role": Role.TOOL,
                        "tool_call_id": call_id,
                        "is_error": block.get("is_error") is True,
                        "text": _tool_result_text(block.get("content")),
                    }
                )
        if user_part is not None:
            user_part["text"] = _join(texts)
        return parts

    def _assistant_parts(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        texts: list[str] = []
        thoughts: list[str] = []
        calls: list[ToolCall] = []
        for block in blocks:
            btype = block.get("type")
            if btype == "text":
                if isinstance(block.get("text"), str) and block["text"]:
                    texts.append(block["text"])
            elif btype == "thinking":
                if isinstance(block.get("thinking"), str) and block["thinking"]:
                    thoughts.append(block["thinking"])
            elif btype == "tool_use":
                call_id, name = block.get("id"), block.get("name")
                if not isinstance(call_id, str) or not isinstance(name, str):
                    raise self.fail("tool_use has no string id and name")
                calls.append(
                    ToolCall(id=call_id, name=name, arguments=json.dumps(block.get("input", {})))
                )
        if not (texts or thoughts or calls):
            return []
        return [
            {
                "role": Role.ASSISTANT,
                "text": _join(texts),
                "reasoning": _join(thoughts) if thoughts else None,
                "tool_calls": calls,
            }
        ]


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif item.get("type") == "image":
                parts.append("[image]")
    return _join(parts)


def _attach_usage(messages: list[Message], records: list[_UsageRecord]) -> None:
    """Put each requestId group's usage on its last message in merged order (see docstring).

    ``records`` are the surviving usage records in merged order.
    """
    usage_by_request = {r.request_id: r.usage for r in records}  # the last record wins
    last: dict[str, Message] = {}
    for msg in messages:
        if msg.role is Role.ASSISTANT and msg.request_id is not None:
            msg.usage = None
            last[msg.request_id] = msg
    for request_id, usage in usage_by_request.items():
        if request_id in last:
            last[request_id].usage = usage
        else:
            logger.debug("dropping usage of requestId %r: no messages in group", request_id)


def _build_session(session_id: str, files: list[_ParsedFile]) -> Session:
    """Merge the parsed files of one session (``files`` is in sorted filename order)."""
    main = next((f for f in files if f.path.stem == session_id), files[0])
    messages: list[Message] = []
    records: list[_UsageRecord] = []
    seen: set[str] = set()
    for parsed in files:
        records.extend(r for r in parsed.usage_records if r.uuid not in seen)
        for msg, uuid in zip(parsed.messages, parsed.record_uuids, strict=True):
            if uuid in seen:
                logger.debug(
                    "%s: skipping message %r of repeated uuid %r", parsed.path, msg.id, uuid
                )
                continue
            messages.append(msg)
        seen |= parsed.kept_uuids
    if len(files) > 1:
        # Stable: file order breaks ties; untimestamped messages go last.
        messages.sort(key=lambda m: (m.timestamp is None, m.timestamp))
        records.sort(key=lambda r: (r.timestamp is None, r.timestamp))
    _attach_usage(messages, records)
    stamps = [m.timestamp for m in messages if m.timestamp is not None]
    cwd = main.cwd
    return Session(
        id=session_id,
        harness=HARNESS,
        project=(PurePosixPath(cwd).name or None) if cwd else None,
        cwd=cwd,
        title=main.title,
        started_at=min(stamps) if stamps else None,
        ended_at=max(stamps) if stamps else None,
        git_branch=main.git_branch,
        messages=messages,
    )


def read_session(path: str | os.PathLike[str]) -> Session:
    """Read one Claude Code session file into a :class:`Session`."""
    parsed = _FileParser(Path(path)).parse()
    return _build_session(parsed.session_id, [parsed])


def read_project(path: str | os.PathLike[str]) -> list[Session]:
    """Read every ``*.jsonl`` directly inside ``path`` (non-recursive) into sessions.

    Files sharing a ``sessionId`` (e.g. subagent ``agent-*.jsonl``) merge into one session.
    Sessions are sorted by ``(started_at is None, started_at, id)``.
    """
    files = sorted(p for p in Path(path).glob("*.jsonl") if p.is_file())
    groups: dict[str, list[_ParsedFile]] = {}
    for file in files:
        parsed = _FileParser(file).parse()
        groups.setdefault(parsed.session_id, []).append(parsed)
    sessions = [_build_session(sid, group) for sid, group in groups.items()]
    sessions.sort(key=lambda s: (s.started_at is None, s.started_at, s.id))
    return sessions
