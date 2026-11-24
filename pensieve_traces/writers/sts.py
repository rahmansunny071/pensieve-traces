"""STS writer: canonical :class:`~pensieve_traces.Session` to a Hugging Face session trace.

Spec: https://huggingface.co/docs/hub/main/en/session-traces-format (how the Hub reads agent
traces: https://huggingface.co/docs/hub/main/en/agent-traces).

The output is JSONL. The first line is the session header (``type``, ``harness``, ``id`` and,
when the session has a title, ``name``); :func:`write_session` also adds the extra header key
``pensieveRedacted`` (bool: whether redaction ran), which the spec allows on the header line and
``pensieve-traces upload`` reads. This reverses #4's "no extra header keys" decision. Every other line is
``{"type": "message", "message": {...}}`` holding only the spec's message keys: ``role``,
``content``, ``reasoningContent``, ``toolCalls``, ``toolCallId``, ``timestamp`` (epoch
milliseconds) and ``model``. Optional keys are omitted when empty.

Canonical fields with no STS equivalent are dropped, not moved into extra fields: ``parent_id``,
``usage``, ``request_id``, ``is_meta``, ``is_error``, ``is_sidechain``, message and session
``metadata``, ``cwd``, ``project``, ``git_branch``, ``started_at`` and ``ended_at``. Meta
messages are still written. Sidechain (subagent) messages are skipped unless requested.

:func:`session_to_sts_lines` is a pure mapper and does no redaction; :func:`write_session` always
goes through :func:`~pensieve_traces.prepare_for_output`.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from pensieve_traces.model import Message, Session
from pensieve_traces.redact import Anonymizer, prepare_for_output

__all__ = ["session_to_sts_lines", "write_session"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


def _epoch_ms(value: datetime) -> int:
    return (value - _EPOCH) // _MS  # integer arithmetic: no float rounding


def _message_dict(msg: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": msg.role.value, "content": msg.text}
    if msg.reasoning is not None:
        out["reasoningContent"] = msg.reasoning
    if msg.tool_calls:
        out["toolCalls"] = [
            {"id": c.id, "function": {"name": c.name, "arguments": c.arguments or "{}"}}
            for c in msg.tool_calls
        ]
    if msg.tool_call_id is not None:
        out["toolCallId"] = msg.tool_call_id
    if msg.timestamp is not None:
        out["timestamp"] = _epoch_ms(msg.timestamp)
    if msg.model is not None:
        out["model"] = msg.model
    return out


def session_to_sts_lines(
    session: Session, *, include_sidechains: bool = False, redacted: bool | None = None
) -> list[dict]:
    """Map ``session`` to STS lines (header first). Does not redact.

    ``redacted`` sets the header key ``pensieveRedacted``; ``None`` omits it.
    """
    header: dict[str, Any] = {
        "type": "session",
        "harness": session.harness or "unknown",
        "id": session.id,
    }
    if session.title is not None:
        header["name"] = session.title
    if redacted is not None:
        header["pensieveRedacted"] = redacted
    lines = [header]
    for msg in session.messages:
        if msg.is_sidechain and not include_sidechains:
            continue
        lines.append({"type": "message", "message": _message_dict(msg)})
    return lines


def write_session(
    session: Session,
    path: str | os.PathLike[str],
    *,
    redact: bool = True,
    include_sidechains: bool = False,
    anonymizer: Anonymizer | None = None,
) -> None:
    """Write ``session`` as an STS JSONL file. Redacts by default.

    Raises :class:`~pensieve_traces.RedactionUnavailableError` (before the file is opened) if
    redaction is on and teich is not installed.
    """
    prepared = prepare_for_output(session, redact=redact, anonymizer=anonymizer)
    lines = session_to_sts_lines(prepared, include_sidechains=include_sidechains, redacted=redact)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
