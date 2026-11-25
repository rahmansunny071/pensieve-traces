"""Pinned coverage of ``redact_session`` (teich plus the email-before-period pre-pass) on chat-style
personal data (needs the `redact` extra).

A kind counts as replaced when its exact value is gone from that message's redacted text. The
expectation is measured with teich 0.3.8 (the version ``uv.lock`` pins); a teich upgrade that
changes coverage fails this test, and the README "Redaction" table must change with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pensieve_traces.model import Session
from pensieve_traces.redact import redact_session

FIXTURE = Path(__file__).parent / "fixtures" / "redact" / "chat_pii_session.json"

# kind (also the fixture message id) -> the fictional value placed in that message
VALUES = {
    "email": "jane.doe@vexlor-mail.invalid",
    "ssn_dashed": "987-65-4320",
    "ssn_undashed": "987654320",
    "iban": "GB82WEST12345698765432",
    "card_number": "4111 1111 1111 1111",
    "phone_call_me_at_10_digit": "(555) 555-0142",
    "phone_my_phone_is_7_digit": "555-0143",
    "address_after_i_live_at": "42 Maple Lane",
    "address_send_it_to": "42 Maple Lane",
    "person_name": "Jane Doe",
    "health_detail": "type 2 diabetes",
    "money_amount": "$4,200",
    "email_period": "sam.lee@vexlor-mail.invalid",
    "email_period_paren": "sam.lee@vexlor-mail.invalid",
    "email_period_quote": "sam.lee@vexlor-mail.invalid",
    "email_curly_quote": "sam.lee@vexlor-mail.invalid",
    "email_exclaim": "sam.lee@vexlor-mail.invalid",
    "email_question": "sam.lee@vexlor-mail.invalid",
    "email_colon": "sam.lee@vexlor-mail.invalid",
    "email_semicolon": "sam.lee@vexlor-mail.invalid",
}

EXPECTED_COVERAGE = {
    "email": True,
    "email_period": True,
    "email_period_paren": True,
    "email_period_quote": True,
    "email_curly_quote": True,
    "email_exclaim": True,  # regression guards: teich 0.3.8 already replaces these
    "email_question": True,
    "email_colon": True,
    "email_semicolon": True,
    "ssn_dashed": True,
    "phone_call_me_at_10_digit": True,
    "address_after_i_live_at": True,
    "ssn_undashed": False,
    "address_send_it_to": False,
    "iban": False,
    "card_number": False,
    "phone_my_phone_is_7_digit": False,
    "person_name": False,
    "health_detail": False,
    "money_amount": False,
}


def test_chat_pii_coverage_is_pinned() -> None:
    pytest.importorskip("teich.anonymize")
    session = Session.from_json(FIXTURE.read_text(encoding="utf-8"))
    originals = {m.id: m.text for m in session.messages}
    assert set(originals) == set(VALUES)
    for kind, value in VALUES.items():
        assert value in originals[kind], f"fixture message {kind} lacks its value"
    redacted = {m.id: m.text for m in redact_session(session).messages}
    actual = {kind: value not in redacted[kind] for kind, value in VALUES.items()}
    assert actual == EXPECTED_COVERAGE


def test_trailing_period_email_gets_the_bare_address_placeholder() -> None:
    pytest.importorskip("teich.anonymize")
    from teich.anonymize import TraceAnonymizer

    addr = "sam.lee@vexlor-mail.invalid"
    anon = TraceAnonymizer()
    placeholder = anon.anonymize_text(addr)
    assert placeholder != addr
    session = Session.from_json(FIXTURE.read_text(encoding="utf-8"))
    redacted = {m.id: m.text for m in redact_session(session, anon).messages}
    assert redacted["email_period"] == f"Mail {placeholder}."
    assert redacted["email_period_paren"] == f"(contact {placeholder}.)"
    assert redacted["email_curly_quote"] == f"She wrote \u201c{placeholder}.\u201d"


@pytest.mark.parametrize(
    "text",
    [
        "export DB_PASSWORD=P@ssw0rd.!Abc9",
        "password=admin@db.internal.(Xy9z)",
        "password=P@ssw0rd.!Abc9",
    ],
)
def test_guard_never_redacts_less_than_teich_alone(text: str) -> None:
    pytest.importorskip("teich.anonymize")
    from teich.anonymize import TraceAnonymizer

    session = Session.from_json(FIXTURE.read_text(encoding="utf-8"))
    session.messages = session.messages[:1]
    session.messages[0].text = text
    guarded = redact_session(session, TraceAnonymizer()).messages[0].text
    assert guarded == TraceAnonymizer().anonymize_text(text)
    for tail in (".!Abc9", "(Xy9z)", "Abc9"):
        assert tail not in guarded
