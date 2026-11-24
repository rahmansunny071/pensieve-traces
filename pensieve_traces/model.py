"""Canonical, harness-neutral session model.

Every reader produces a :class:`Session` and every writer consumes one; formats are never
converted directly to each other. The model holds raw text and tool output and does no
redaction: run the redaction pass before any writer.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


def _check_aware(value: datetime | None, name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be a timezone-aware datetime")


def _dt_to_str(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _dt_from_str(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
            cache_creation_tokens=data.get("cache_creation_tokens", 0),
        )


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""  # JSON string, kept verbatim (may be malformed or non-object)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        return cls(id=data["id"], name=data["name"], arguments=data.get("arguments", ""))


@dataclass
class Message:
    id: str
    role: Role
    parent_id: str | None = None
    timestamp: datetime | None = None
    text: str = ""
    reasoning: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    model: str | None = None
    request_id: str | None = None
    usage: Usage | None = None
    is_sidechain: bool = False
    is_meta: bool = False
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.role = Role(self.role)
        _check_aware(self.timestamp, "timestamp")
        if self.tool_call_id is not None and self.role is not Role.TOOL:
            raise ValueError("tool_call_id may only be set on tool messages")
        if self.is_error and self.role is not Role.TOOL:
            raise ValueError("is_error may only be true on tool messages")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role.value,
            "parent_id": self.parent_id,
            "timestamp": _dt_to_str(self.timestamp),
            "text": self.text,
            "reasoning": self.reasoning,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "model": self.model,
            "request_id": self.request_id,
            "usage": None if self.usage is None else self.usage.to_dict(),
            "is_sidechain": self.is_sidechain,
            "is_meta": self.is_meta,
            "is_error": self.is_error,
            "metadata": copy.deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        usage = data.get("usage")
        return cls(
            id=data["id"],
            role=Role(data["role"]),
            parent_id=data.get("parent_id"),
            timestamp=_dt_from_str(data.get("timestamp")),
            text=data.get("text", ""),
            reasoning=data.get("reasoning"),
            tool_calls=[ToolCall.from_dict(c) for c in data.get("tool_calls", [])],
            tool_call_id=data.get("tool_call_id"),
            model=data.get("model"),
            request_id=data.get("request_id"),
            usage=None if usage is None else Usage.from_dict(usage),
            is_sidechain=data.get("is_sidechain", False),
            is_meta=data.get("is_meta", False),
            is_error=data.get("is_error", False),
            metadata=copy.deepcopy(data.get("metadata", {})),
        )


@dataclass
class Session:
    id: str
    harness: str | None = None
    project: str | None = None
    cwd: str | None = None
    title: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    git_branch: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    # Lazily built id -> message index; not part of equality, repr or serialisation.
    _index: dict[str, Message] = field(default_factory=dict, init=False, repr=False, compare=False)
    _index_key: tuple[tuple[int, str], ...] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _check_aware(self.started_at, "started_at")
        _check_aware(self.ended_at, "ended_at")
        seen: set[str] = set()
        for msg in self.messages:
            if msg.id in seen:
                raise ValueError(f"duplicate message id: {msg.id!r}")
            seen.add(msg.id)

    # -- lookups ---------------------------------------------------------

    def _by_id(self) -> dict[str, Message]:
        # Rebuild whenever the message objects or their ids change (not just the count).
        key = tuple((id(m), m.id) for m in self.messages)
        if key != self._index_key:
            index: dict[str, Message] = {}
            for m in self.messages:
                if m.id in index:
                    raise ValueError(f"duplicate message id: {m.id!r}")
                index[m.id] = m
            self._index, self._index_key = index, key
        return self._index

    def message(self, id: str) -> Message | None:
        return self._by_id().get(id)

    def parent_of(self, msg: Message) -> Message | None:
        return None if msg.parent_id is None else self.message(msg.parent_id)

    def children_of(self, msg: Message) -> list[Message]:
        return [m for m in self.messages if m.parent_id == msg.id]

    def tool_calls(self) -> list[ToolCall]:
        return [c for m in self.messages for c in m.tool_calls]

    def tool_call(self, call_id: str) -> ToolCall | None:
        return next((c for c in self.tool_calls() if c.id == call_id), None)

    def tool_result_for(self, call_id: str) -> Message | None:
        return next(
            (m for m in self.messages if m.role is Role.TOOL and m.tool_call_id == call_id),
            None,
        )

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "harness": self.harness,
            "project": self.project,
            "cwd": self.cwd,
            "title": self.title,
            "started_at": _dt_to_str(self.started_at),
            "ended_at": _dt_to_str(self.ended_at),
            "git_branch": self.git_branch,
            "metadata": copy.deepcopy(self.metadata),
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=data["id"],
            harness=data.get("harness"),
            project=data.get("project"),
            cwd=data.get("cwd"),
            title=data.get("title"),
            started_at=_dt_from_str(data.get("started_at")),
            ended_at=_dt_from_str(data.get("ended_at")),
            git_branch=data.get("git_branch"),
            metadata=copy.deepcopy(data.get("metadata", {})),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
        )

    def to_json(self, **kwargs: Any) -> str:
        """Serialise to JSON; raises ``TypeError`` if any ``metadata`` is not JSON-serialisable."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, text: str) -> Session:
        return cls.from_dict(json.loads(text))
