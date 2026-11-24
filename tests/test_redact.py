"""Redaction tests. Synthetic data only; key shapes are assembled at runtime from fragments."""

from __future__ import annotations

import json
import sys
import types
import warnings
from pathlib import Path
from typing import Any

import pytest

import pensieve_traces
from pensieve_traces import (
    RedactionUnavailableError,
    Session,
    prepare_for_output,
    redact_session,
)

FIXTURE = Path(__file__).parent / "fixtures" / "redact" / "secrets_session.json"

# Realistic key shapes, built from fragments so no committed file contains one.
SECRETS = {
    "GH_TOKEN": "gh" + "p_" + "R7kQ2vXm9LpT4wZs1NdB6yHcJ8aF3eUoG5tY",
    "ANT_KEY": "sk-" + "ant-" + "api03-" + "Xt9QmZ4vL2pRk7WnB1dHs6YcJ3aFeUoG8TyN5qMw0xKzVb",
    "AWS_KEY": "AK" + "IA" + "Q7XM2VK9LPT4WZS1",
}


def load_session() -> Session:
    text = FIXTURE.read_text()
    for name, value in SECRETS.items():
        text = text.replace("{" + name + "}", value)
    return Session.from_json(text)


class StubAnonymizer:
    """Stateful stub: counts mapped values and injects JSON-hostile characters."""

    def __init__(self) -> None:
        self.map: dict[str, str] = {}
        self.seen_values: list[Any] = []

    def anonymize_text(self, text: str) -> str:
        out = text
        for secret in ("jdoe", *SECRETS.values()):
            if secret in out:
                token = self.map.setdefault(secret, f'<R{len(self.map)}"\\>')
                out = out.replace(secret, token)
        return out

    def anonymize_value(self, value: Any) -> Any:
        self.seen_values.append(value)
        if isinstance(value, str):
            return self.anonymize_text(value)
        if isinstance(value, list):
            return [self.anonymize_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.anonymize_value(v) for k, v in value.items()}
        return value


def test_stub_sees_every_field_and_input_is_not_mutated():
    session = load_session()
    before = session.to_dict()
    stub = StubAnonymizer()
    out = redact_session(session, stub)

    assert session.to_dict() == before
    assert out is not session
    dumped = out.to_json()
    assert "jdoe" not in dumped
    for secret in SECRETS.values():
        assert secret not in dumped
    assert out.messages[0].metadata != session.messages[0].metadata
    assert out.metadata != session.metadata
    assert out.title != session.title and "jdoe" not in out.title
    # same input -> same placeholder (stateful, one instance for the session)
    assert out.messages[0].text.count("<R") == 2
    assert len(stub.map) == len({v for v in stub.map.values()})

    # untouched fields
    for a, b in zip(session.messages, out.messages):
        assert (a.id, a.parent_id, a.tool_call_id, a.request_id, a.model, a.usage, a.role) == (
            b.id,
            b.parent_id,
            b.tool_call_id,
            b.request_id,
            b.model,
            b.usage,
            b.role,
        )
        assert [(c.id, c.name) for c in a.tool_calls] == [(c.id, c.name) for c in b.tool_calls]
        assert (a.is_sidechain, a.is_meta, a.is_error) == (b.is_sidechain, b.is_meta, b.is_error)
    assert (out.id, out.harness, out.started_at) == (
        session.id,
        session.harness,
        session.started_at,
    )


def test_arguments_stay_valid_json_even_when_stub_injects_quotes_and_backslashes():
    session = load_session()
    session.messages[1].tool_calls[0].arguments = json.dumps({"command": "cat /Users/jdoe/x"})
    out = redact_session(session, StubAnonymizer())
    for call in out.messages[1].tool_calls:
        json.loads(call.arguments)
    assert "jdoe" not in out.messages[1].tool_calls[0].arguments


def test_unchanged_arguments_keep_original_formatting():
    session = load_session()
    raw = '{"a":   1,\n "b": "plain"}'
    session.messages[1].tool_calls[0].arguments = raw
    out = redact_session(session, StubAnonymizer())
    assert out.messages[1].tool_calls[0].arguments == raw


def test_non_json_arguments_fall_back_to_text():
    session = load_session()
    session.messages[1].tool_calls[0].arguments = "not json jdoe"
    out = redact_session(session, StubAnonymizer())
    assert "jdoe" not in out.messages[1].tool_calls[0].arguments


def test_project_follows_redacted_cwd_basename():
    session = load_session()
    out = redact_session(session, StubAnonymizer())
    assert out.cwd is not None and out.project is not None
    assert out.project == out.cwd.rsplit("/", 1)[-1]


def _install_fake_teich(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    instances: list[Any] = []

    class FakeTraceAnonymizer(StubAnonymizer):
        def __init__(self) -> None:
            super().__init__()
            instances.append(self)

    fake = types.ModuleType("teich.anonymize")
    fake.TraceAnonymizer = FakeTraceAnonymizer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "teich", types.ModuleType("teich"))
    monkeypatch.setitem(sys.modules, "teich.anonymize", fake)
    return instances


def test_default_path_builds_a_fresh_anonymizer_per_call(monkeypatch):
    instances = _install_fake_teich(monkeypatch)
    session = load_session()
    redact_session(session)
    redact_session(session)
    assert len(instances) == 2 and instances[0] is not instances[1]


def test_missing_teich_raises_with_hint_and_cause(monkeypatch):
    monkeypatch.setitem(sys.modules, "teich", None)
    monkeypatch.setitem(sys.modules, "teich.anonymize", None)
    with pytest.raises(RedactionUnavailableError) as info:
        redact_session(load_session())
    assert 'pip install "pensieve-traces[redact]"' in str(info.value)
    assert isinstance(info.value, ImportError)
    assert isinstance(info.value.__cause__, ImportError)


def test_prepare_for_output_redacts_by_default():
    out = prepare_for_output(load_session(), anonymizer=StubAnonymizer())
    assert "jdoe" not in out.to_json()


def test_prepare_for_output_without_teich_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "teich", None)
    monkeypatch.setitem(sys.modules, "teich.anonymize", None)
    with pytest.raises(RedactionUnavailableError):
        prepare_for_output(load_session())


def test_prepare_for_output_opt_out_warns_and_returns_same_session():
    session = load_session()
    with pytest.warns(UserWarning) as record:
        out = prepare_for_output(session, redact=False)
    assert out is session
    assert str(record[0].message) == (
        "Redaction disabled: output may contain secrets, emails and local paths."
    )


def test_prepare_for_output_redact_true_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        prepare_for_output(load_session(), anonymizer=StubAnonymizer())


def test_public_exports():
    for name in ("redact_session", "prepare_for_output", "RedactionUnavailableError"):
        assert name in pensieve_traces.__all__


# -- real teich (skipped unless the `redact` extra is installed) ---------------------------


@pytest.fixture
def real_session() -> Session:
    pytest.importorskip("teich.anonymize")
    return load_session()


def test_real_teich_redacts_fixture(real_session):
    out = redact_session(real_session)
    dumped = out.to_json()
    for secret in SECRETS.values():
        assert secret not in dumped
    assert "jdoe" not in dumped
    assert "vexlor-mail.io" not in dumped
    assert out.cwd is not None and "jdoe" not in out.cwd
    assert out.project is not None and "jdoe" not in out.project


def test_real_teich_arguments(real_session):
    out = redact_session(real_session)
    calls = out.messages[1].tool_calls
    for call in calls:
        json.loads(call.arguments)
    # email after an escaped newline
    assert "jdoe@vexlor-mail.io" not in calls[0].arguments
    # password=... inside a JSON string
    assert "Zq8vT3mXk91pLw" not in calls[1].arguments
    # numeric secret value under a key-aware name
    assert "7340192856" not in calls[2].arguments


def test_real_teich_placeholders_are_consistent(real_session):
    out = redact_session(real_session)
    email = real_session.metadata["contact"]
    assert email == "jdoe@vexlor-mail.io"
    placeholder = out.metadata["contact"]
    assert placeholder != email
    assert placeholder in out.title
    assert placeholder in out.messages[0].text
    assert placeholder in out.messages[0].metadata["note"]
