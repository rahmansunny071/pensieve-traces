"""Redaction pass for canonical sessions, built on teich's ``TraceAnonymizer``.

``teich`` is an optional dependency (``pip install "pensieve-traces[redact]"``) and is imported
lazily. Every writer must obtain its session through :func:`prepare_for_output`, which redacts
by default, warns when redaction is disabled and never falls back to unredacted output when
redaction cannot run.

teich 0.3.8 leaves an email address unredacted when a period follows it directly (``mail a@b.invalid.``);
:class:`_EmailPeriodGuard` closes that gap before teich runs, with the same placeholder as the bare
address. Redaction is best-effort pattern matching: review anything before you publish it. It was tuned
on coding sessions and misses much chat personal data (see the README "Redaction" table).
"""

from __future__ import annotations

import copy
import json
import re
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


# teich's email local part and its lookbehind, then a domain, then the period(s) teich fails on. The
# lookahead is teich's own boundary without the dot, so ``a@b.invalid.com`` (teich reads ``.com`` as
# part of the domain) is left to teich while ``a@b.invalid.`` and ``a@b.invalid.\u201d`` are caught.
_EMAIL_DOT = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9][A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]*"
    r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)(\.+)(?![A-Za-z0-9_%+-])"
)


class _EmailPeriodGuard:
    """Wraps an anonymizer so an email directly followed by ``.`` is redacted too.

    Each such address is handed to the wrapped anonymizer on its own (exactly what teich matches
    without the period), so it gets the same placeholder as the bare address anywhere in the
    session, and across sessions that share the wrapped anonymizer. The dots are kept.
    """

    def __init__(self, inner: Anonymizer) -> None:
        self._inner = inner

    def _pre(self, text: str) -> str:
        if "@" not in text:
            return text
        return _EMAIL_DOT.sub(lambda m: self._inner.anonymize_text(m[1]) + m[2], text)

    def _walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._pre(value)
        if isinstance(value, list):
            return [self._walk(v) for v in value]
        if isinstance(value, dict):
            # Rebuilt, not mutated; keys change only when they hold such an email, so the
            # key-aware rules inside teich still see the original key names.
            return {
                (self._pre(k) if isinstance(k, str) else k): self._walk(v) for k, v in value.items()
            }
        return value

    def anonymize_text(self, text: str) -> str:
        return self._inner.anonymize_text(self._pre(text))

    def anonymize_value(self, value: Any) -> Any:
        return self._inner.anonymize_value(self._walk(value))


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _DuplicateKeyError("duplicate key")
    return dict(pairs)


def _redact_arguments(anonymizer: Anonymizer | _EmailPeriodGuard, arguments: str) -> str:
    if not arguments:
        return arguments
    try:
        decoded = json.loads(arguments, object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKeyError:
        # json.loads keeps only the last duplicate key, so comparing the decoded value with its
        # redaction would miss secrets in the earlier, shadowed values and the original string
        # (which still contains them) would be returned. Re-encode the redacted last-wins value
        # instead: shadowed duplicates are dropped, so no original text can survive.
        try:
            decoded = json.loads(arguments)
        except ValueError:
            # Invalid JSON that also has a duplicate key before the break: plain-text fallback.
            return anonymizer.anonymize_text(arguments)
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

    Text goes through :class:`_EmailPeriodGuard` first, so an email followed directly by a period
    (which teich 0.3.8 misses) is replaced with the bare address's placeholder.

    With ``anonymizer=None`` a fresh teich ``TraceAnonymizer`` is built per call, so placeholders
    are consistent within the session. Sharing one injected anonymizer across sessions links
    their placeholders.

    Raises :class:`RedactionUnavailableError` if teich is needed but not installed.
    """
    anon = _EmailPeriodGuard(anonymizer if anonymizer is not None else _default_anonymizer())
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
