"""Redaction pass for canonical sessions, built on teich's ``TraceAnonymizer``.

``teich`` is an optional dependency (``pip install "pensieve-traces[redact]"``) and is imported
lazily. Every writer must obtain its session through :func:`prepare_for_output`, which redacts
by default, warns when redaction is disabled and never falls back to unredacted output when
redaction cannot run.

Redaction is best-effort pattern matching: review anything before you publish it.
"""

from __future__ import annotations

import copy
import json
import warnings
from pathlib import PurePosixPath
from typing import Any, Protocol

from pensieve_traces.model import Session

__all__ = ["Anonymizer", "RedactionUnavailableError", "prepare_for_output", "redact_session"]

_INSTALL_HINT = 'Redaction needs the optional dependency: pip install "pensieve-traces[redact]"'
_DISABLED_WARNING = "Redaction disabled: output may contain secrets, emails and local paths."


class RedactionUnavailableError(ImportError):
    """Raised when redaction is requested but teich is not importable."""


class Anonymizer(Protocol):
    """The subset of teich's ``TraceAnonymizer`` used here."""

    def anonymize_text(self, text: str) -> str: ...

    def anonymize_value(self, value: Any) -> Any: ...


def _default_anonymizer() -> Anonymizer:
    try:
        from teich.anonymize import TraceAnonymizer
    except ImportError as exc:
        raise RedactionUnavailableError(_INSTALL_HINT) from exc
    return TraceAnonymizer()


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _DuplicateKeyError("duplicate key")
    return dict(pairs)


def _redact_arguments(anonymizer: Anonymizer, arguments: str) -> str:
    if not arguments:
        return arguments
    try:
        decoded = json.loads(arguments, object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKeyError:
        # json.loads keeps only the last duplicate key, so comparing the decoded value with its
        # redaction would miss secrets in the earlier, shadowed values and the original string
        # (which still contains them) would be returned. Re-encode the redacted last-wins value
        # instead: shadowed duplicates are dropped, so no original text can survive.
        decoded = json.loads(arguments)
        return json.dumps(anonymizer.anonymize_value(decoded))
    except ValueError:
        return anonymizer.anonymize_text(arguments)
    redacted = anonymizer.anonymize_value(copy.deepcopy(decoded))
    if redacted == decoded:
        return arguments  # nothing to redact: keep the original formatting
    return json.dumps(redacted)


def redact_session(session: Session, anonymizer: Anonymizer | None = None) -> Session:
    """Return a redacted deep copy of ``session``; the input is not mutated.

    Redacts ``cwd`` (first), ``project``, ``title``, ``git_branch``, message text (tool results
    included), ``reasoning``, tool-call arguments and ``metadata`` values. Ids, ``ToolCall.name``,
    ``harness``, ``model``, timestamps, usage, roles and boolean flags are left as they are.

    With ``anonymizer=None`` a fresh teich ``TraceAnonymizer`` is built per call, so placeholders
    are consistent within the session. Sharing one injected anonymizer across sessions links
    their placeholders.

    Raises :class:`RedactionUnavailableError` if teich is needed but not installed.
    """
    anon = anonymizer if anonymizer is not None else _default_anonymizer()
    out = copy.deepcopy(session)

    original_cwd = session.cwd
    if out.cwd is not None:
        out.cwd = anon.anonymize_text(out.cwd)
    if out.project is not None:
        if original_cwd and out.cwd and session.project == PurePosixPath(original_cwd).name:
            out.project = PurePosixPath(out.cwd).name
        else:
            out.project = anon.anonymize_text(out.project)
    if out.title is not None:
        out.title = anon.anonymize_text(out.title)
    if out.git_branch is not None:
        out.git_branch = anon.anonymize_text(out.git_branch)
    if out.metadata:
        out.metadata = anon.anonymize_value(out.metadata)

    for msg in out.messages:
        msg.text = anon.anonymize_text(msg.text)
        if msg.reasoning is not None:
            msg.reasoning = anon.anonymize_text(msg.reasoning)
        for call in msg.tool_calls:
            call.arguments = _redact_arguments(anon, call.arguments)
        if msg.metadata:
            msg.metadata = anon.anonymize_value(msg.metadata)
    return out


def prepare_for_output(
    session: Session, *, redact: bool = True, anonymizer: Anonymizer | None = None
) -> Session:
    """The single output gate every writer must call.

    ``redact=True`` returns :func:`redact_session` (raising
    :class:`RedactionUnavailableError` if teich is missing; it never returns unredacted data).
    ``redact=False`` returns ``session`` unchanged and emits a ``UserWarning``.
    """
    if redact:
        return redact_session(session, anonymizer)
    warnings.warn(_DISABLED_WARNING, UserWarning, stacklevel=2)
    return session
