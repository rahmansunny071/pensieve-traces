"""ConvoMem conversations writer: canonical sessions to ConvoMem-style conversations.

Layout: the ConvoMem dataset (https://huggingface.co/datasets/Salesforce/ConvoMem), as read by
convomem-arena's ``parse_conversation`` / ``parse_filler_file``. A conversation is
``{"id": str, "containsEvidence": bool, "messages": [{"speaker": "User" | "Assistant", "text": str}]}``
and a filler file wraps conversations as ``{"evidence_items": [{"conversations": [...]}]}``.

One session is one conversation (``id`` is the session id), ordered by session start time. Only
user prompts and assistant prose are kept: meta messages, sidechain messages, system messages,
tool results and empty or whitespace-only turns are dropped. With ``tool_activity="summarize"`` each
tool call adds a one-line ``[ran NAME: ARG]`` to its assistant turn; ``"drop"`` omits them.

The message-ID map ``{conversation id: [canonical message id, ...]}`` is index-aligned with each
conversation's ``messages`` so later Q/A generation (#8) can point evidence at exact messages.

:func:`sessions_to_conversations` and :func:`sessions_to_conversations_with_ids` are pure mappers
and do no redaction; :func:`write_conversations` always goes through
:func:`~pensieve_traces.prepare_for_output`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pensieve_traces.model import Message, Role, Session
from pensieve_traces.redact import Anonymizer, _default_anonymizer, prepare_for_output

__all__ = [
    "CONVERSATIONS_FILE",
    "MESSAGE_IDS_FILE",
    "sessions_to_conversations",
    "sessions_to_conversations_with_ids",
    "write_conversations",
]

CONVERSATIONS_FILE = "conversations.json"
MESSAGE_IDS_FILE = "conversations.message_ids.json"
_TOOL_MODES = ("summarize", "drop")
_ARG_LIMIT = 80
_SPEAKERS = {Role.USER: "User", Role.ASSISTANT: "Assistant"}


def _tool_line(name: str, arguments: str) -> str:
    try:
        decoded = json.loads(arguments) if arguments else None
    except ValueError:
        decoded = None
    command = decoded.get("command") if isinstance(decoded, dict) else None
    if isinstance(command, str) and command.strip():
        arg = " ".join(command.split())
        if len(arg) > _ARG_LIMIT:
            arg = arg[: _ARG_LIMIT - 3] + "..."
        return f"[ran {name}: {arg}]"
    return f"[ran {name}]"


def _message_text(msg: Message, tool_activity: str) -> str:
    parts = [msg.text.strip()]
    if tool_activity == "summarize" and msg.role is Role.ASSISTANT:
        parts.extend(_tool_line(c.name, c.arguments) for c in msg.tool_calls)
    return "\n".join(p for p in parts if p)


def sessions_to_conversations_with_ids(
    sessions: list[Session], tool_activity: str = "summarize"
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Map ``sessions`` to ``(conversations, message_id_map)``. Does not redact."""
    if tool_activity not in _TOOL_MODES:
        raise ValueError(f"tool_activity must be one of {_TOOL_MODES}, not {tool_activity!r}")
    ordered = sorted(sessions, key=lambda s: (s.started_at is None, s.started_at, s.id))
    conversations: list[dict[str, Any]] = []
    id_map: dict[str, list[str]] = {}
    for session in ordered:
        messages: list[dict[str, str]] = []
        ids: list[str] = []
        for msg in session.messages:
            speaker = _SPEAKERS.get(msg.role)
            if speaker is None or msg.is_meta or msg.is_sidechain:
                continue
            text = _message_text(msg, tool_activity)
            if not text:
                continue
            messages.append({"speaker": speaker, "text": text})
            ids.append(msg.id)
        if messages:
            conversations.append(
                {"id": session.id, "containsEvidence": False, "messages": messages}
            )
            id_map[session.id] = ids
    return conversations, id_map


def sessions_to_conversations(
    sessions: list[Session], tool_activity: str = "summarize"
) -> list[dict[str, Any]]:
    """Map ``sessions`` to ConvoMem conversations. Does not redact."""
    return sessions_to_conversations_with_ids(sessions, tool_activity)[0]


def write_conversations(
    sessions: list[Session],
    out_dir: str | os.PathLike[str],
    *,
    tool_activity: str = "summarize",
    redact: bool = True,
    anonymizer: Anonymizer | None = None,
) -> tuple[Path, Path]:
    """Write ``conversations.json`` and ``conversations.message_ids.json`` into ``out_dir``.

    Redacts by default, with ONE anonymizer shared across all sessions so placeholders are
    consistent across conversations. Raises :class:`~pensieve_traces.RedactionUnavailableError`
    if teich is needed but missing, and ``FileExistsError`` if either target exists; in both
    cases nothing is written. ``out_dir`` must exist.
    """
    if redact and anonymizer is None:
        anonymizer = _default_anonymizer()
    prepared = [prepare_for_output(s, redact=redact, anonymizer=anonymizer) for s in sessions]
    conversations, id_map = sessions_to_conversations_with_ids(prepared, tool_activity)
    conv_path = Path(out_dir) / CONVERSATIONS_FILE
    ids_path = Path(out_dir) / MESSAGE_IDS_FILE
    for path in (conv_path, ids_path):
        if path.exists():
            raise FileExistsError(f"{path} already exists; remove it or choose another --out")
    payload = {"evidence_items": [{"conversations": conversations}]}
    # "x" mode: never replace a file that appeared since the check above.
    with open(conv_path, "x", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(ids_path, "x", encoding="utf-8", newline="\n") as fh:
        json.dump(id_map, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return conv_path, ids_path
