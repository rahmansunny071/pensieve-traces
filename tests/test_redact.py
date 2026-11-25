"""Redaction tests. Synthetic data only; key shapes are assembled at runtime from fragments."""

from __future__ import annotations

import json
import re
import sys
import types
import warnings
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

import pensieve_traces
from pensieve_traces import (
    RedactionUnavailableError,
    Session,
    ToolCall,
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


def _args_out(raw: str) -> str:
    session = load_session()
    session.messages[1].tool_calls[0].arguments = raw
    return redact_session(session, StubAnonymizer()).messages[1].tool_calls[0].arguments


def test_duplicate_json_keys_do_not_leak_shadowed_values():
    leaky = "cat /Users/jdoe/.env; echo " + SECRETS["GH_TOKEN"] + " jdoe@vexlor-mail.io"
    raw = f'{{"command": {json.dumps(leaky)}, "command": "ls"}}'
    out = _args_out(raw)
    json.loads(out)
    assert "jdoe" not in out and SECRETS["GH_TOKEN"] not in out


def test_nested_duplicate_json_keys_do_not_leak():
    aws = SECRETS["AWS_KEY"]
    raw = f'{{"a": {{"k": "jdoe", "k": "ok"}}, "b": [{{"x": "{aws}", "x": 1}}]}}'
    out = _args_out(raw)
    json.loads(out)
    assert "jdoe" not in out and SECRETS["AWS_KEY"] not in out


def test_git_branch_is_redacted():
    session = load_session()
    assert "jdoe" in (session.git_branch or "")
    out = redact_session(session, StubAnonymizer())
    assert out.git_branch is not None and "jdoe" not in out.git_branch


@pytest.mark.parametrize("cwd", ["/Users/jdoe/", "/Users/jdoe"])
def test_project_redacted_with_or_without_trailing_slash(cwd):
    session = Session(id="s", cwd=cwd, project="jdoe")
    out = redact_session(session, StubAnonymizer())
    assert out.project is not None and "jdoe" not in out.project
    assert out.cwd is not None and "jdoe" not in out.cwd
    assert out.project == PurePosixPath(out.cwd).name


@pytest.mark.parametrize(
    "raw",
    [
        '{"x":{"k":1,"k":2},"cmd":"echo jdoe@vexlor-mail.io',
        '{"a":1,"a":2,"m":"jdoe@vexlor-mail.io"} x',
    ],
)
def test_malformed_arguments_with_duplicate_key_fall_back_to_text(raw):
    out = _args_out(raw)
    assert "jdoe" not in out


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
    assert out.git_branch is not None and "vexlor-mail.io" not in out.git_branch
    assert out.git_branch != real_session.git_branch


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


def test_real_teich_project_with_trailing_slash_cwd(real_session):
    out = redact_session(Session(id="s", cwd="/Users/jdoe/", project="jdoe"))
    assert out.cwd is not None and "jdoe" not in out.cwd
    assert out.project is not None and "jdoe" not in out.project
    assert out.project == PurePosixPath(out.cwd).name


@pytest.mark.parametrize(
    "raw",
    [
        '{"x":{"k":1,"k":2},"cmd":"echo jdoe@vexlor-mail.io',
        '{"a":1,"a":2,"m":"jdoe@vexlor-mail.io"} x',
    ],
)
def test_real_teich_malformed_arguments_with_duplicate_key(real_session, raw):
    real_session.messages[1].tool_calls[0].arguments = raw
    out = redact_session(real_session).messages[1].tool_calls[0].arguments
    assert "jdoe@vexlor-mail.io" not in out


_TRAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+'-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*")


class FakeTeich:
    """Mimics teich 0.3.8: lower-cased address map, ``@example.com`` left alone, and no match
    when the address is followed by a period (the bug the guard compensates for)."""

    def __init__(self) -> None:
        self.map: dict[str, str] = {}

    def anonymize_text(self, text: str) -> str:
        def sub(m: re.Match[str]) -> str:
            if text[m.end() : m.end() + 1] == "." and not text[m.end() + 1 : m.end() + 2].isalnum():
                return m[0]
            addr = m[0].lower()
            if addr.endswith("@example.com"):
                return m[0]
            return self.map.setdefault(addr, f"redacted-user{len(self.map) + 1}@example.com")

        return _TRAIL.sub(sub, text)

    def anonymize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.anonymize_text(value)
        if isinstance(value, list):
            return [self.anonymize_value(v) for v in value]
        if isinstance(value, dict):
            return {self.anonymize_text(k): self.anonymize_value(v) for k, v in value.items()}
        return value


ADDR = "sam.lee@vexlor-mail.invalid"


def _session_with(*texts: str) -> Session:
    s = load_session()
    s.messages = s.messages[:1]
    s.messages[0].tool_calls = []
    s.messages[0].metadata = {}
    s.messages[0].reasoning = None
    s.metadata = {}
    s.title = s.cwd = s.project = s.git_branch = None
    s.messages[0].text = " ||| ".join(texts)
    return s


def test_fake_alone_misses_the_trailing_period_like_teich():
    assert FakeTeich().anonymize_text(f"mail {ADDR}.") == f"mail {ADDR}."


@pytest.mark.parametrize(
    "tail",
    [
        ".",
        ".)",
        '."',
        ".\u201d",
        ".\u2019",
        ".`",
        ".*",
        ".|",
        ".<br>",
        "..",
        ".\n",
        ".  next",
        "!",
        "?",
    ],
)
def test_email_followed_by_period_gets_bare_placeholder(tail):
    fake = FakeTeich()
    out = redact_session(_session_with(f"a {ADDR}{tail} x", f"b {ADDR} c"), fake).messages[0].text
    placeholder = fake.map[ADDR]
    assert ADDR not in out
    assert out == f"a {placeholder}{tail} x ||| b {placeholder} c"


def test_email_at_end_of_text_and_other_cases():
    fake = FakeTeich()
    out = (
        redact_session(
            _session_with(f"end {ADDR}.", "o'brien@x.invalid.", "SAM.LEE@VEXLOR-MAIL.INVALID."),
            fake,
        )
        .messages[0]
        .text
    )
    assert "vexlor" not in out.lower() and "brien" not in out
    assert out.count(fake.map[ADDR]) == 2  # lower-cased map: upper-case form shares it


def test_domain_continuation_and_plain_periods_are_left_alone_by_the_guard():
    fake = FakeTeich()
    # the guard must not touch these: no address, or a domain that continues past the period
    # (a@b.invalid.com is teich's to match). me@example.com. is an address followed by a period;
    # the guard hands it to the fake, which leaves @example.com alone, so the text is unchanged.
    text = "v1.2. See file.txt. Mail me@example.com. or a@b.invalid.com"
    out = redact_session(_session_with(text), fake).messages[0].text
    assert out == text.replace("a@b.invalid.com", fake.map["a@b.invalid.com"])
    assert set(fake.map) == {"a@b.invalid.com"}


def test_guard_leaves_assignments_and_non_teich_candidates_to_the_anonymizer():
    fake = FakeTeich()
    # "=" is legal in teich's local part, so the candidate would be "key=sam@...": skip it, or a
    # secret like password=P@ssw0rd.!x would only be redacted in part.
    # f"{ADDR}..x": the period run is followed by a letter, so it is not a boundary (teich's own
    # lookahead rejects "." too); backtracking to one period must not make it one.
    for text in (
        f"key={ADDR}.",
        "password=P@ssw0rd.!Abc9",
        "x@y.in1.",
        "x@localhost.",
        f"{ADDR}..x",
    ):
        out = redact_session(_session_with(text), fake).messages[0].text
        assert out == text, text
    assert fake.map == {}


def test_trailing_period_email_in_every_field_and_arguments():
    fake = FakeTeich()
    s = _session_with(f"t {ADDR}.")
    s.title = f"title {ADDR}."
    s.messages[0].reasoning = f"r {ADDR}."
    s.metadata = {"k": [f"{ADDR}."], f"key {ADDR}.": "v"}
    s.messages[0].metadata = {"m": f"{ADDR}."}
    s.messages[0].tool_calls = [
        ToolCall(id="c1", name="Bash", arguments=json.dumps({"cmd": f"echo {ADDR}."}))
    ]
    before = s.to_dict()
    out = redact_session(s, fake)
    assert s.to_dict() == before
    assert ADDR not in out.to_json()
    assert set(fake.map) == {ADDR}


def test_placeholder_shared_across_sessions_with_one_anonymizer():
    fake = FakeTeich()
    a = redact_session(_session_with(f"x {ADDR}."), fake).messages[0].text
    b = redact_session(_session_with(f"y {ADDR} z"), fake).messages[0].text
    assert a.split()[1] == f"{fake.map[ADDR]}."
    assert b.split()[1] == fake.map[ADDR]


def test_value_keys_not_rewritten_without_an_email_period():
    seen: list[Any] = []

    class Spy(FakeTeich):
        def anonymize_value(self, value: Any) -> Any:
            seen.append(value)
            return super().anonymize_value(value)

    s = _session_with("plain")
    s.metadata = {"password": "p", "nested": {"data": "d"}}
    redact_session(s, Spy())
    assert seen[0] == {"password": "p", "nested": {"data": "d"}}
