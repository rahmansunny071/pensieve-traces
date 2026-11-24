"""STS writer tests. Synthetic fixtures only; offline."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pensieve_traces import Message, Session, ToolCall
from pensieve_traces.readers.claude_code import read_session
from pensieve_traces.writers.sts import session_to_sts_lines, write_session

FIX = Path(__file__).parent / "fixtures" / "sts"
MESSAGE_KEYS = {
    "role",
    "content",
    "reasoningContent",
    "toolCalls",
    "toolCallId",
    "timestamp",
    "model",
}


def golden(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIX / name).read_text().splitlines()]


def fixture_session() -> Session:
    return read_session(FIX / "tool_session.jsonl")


class UpperAnonymizer:
    def anonymize_text(self, text: str) -> str:
        return text.upper()

    def anonymize_value(self, value):
        return value


def test_golden_default_excludes_sidechains() -> None:
    lines = session_to_sts_lines(fixture_session())
    expected = golden("tool_session.sts.jsonl")
    assert len(lines) == len(expected)
    for got, want in zip(lines, expected, strict=True):
        assert got == want


def test_golden_include_sidechains() -> None:
    lines = session_to_sts_lines(fixture_session(), include_sidechains=True)
    expected = golden("tool_session.sidechains.sts.jsonl")
    assert len(lines) == len(expected)
    for got, want in zip(lines, expected, strict=True):
        assert got == want
    assert any(m["message"]["content"] == "subagent task" for m in lines[1:])


def test_header_first_and_shapes() -> None:
    lines = session_to_sts_lines(fixture_session())
    assert lines[0] == {
        "type": "session",
        "harness": "claude_code",
        "id": "sess-sts",
        "name": "List the demo files",
    }
    for line in lines[1:]:
        assert set(line) == {"type", "message"} and line["type"] == "message"
        assert set(line["message"]) <= MESSAGE_KEYS
    call = lines[2]["message"]["toolCalls"][0]
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {"command": "ls"}
    assert lines[3]["message"]["role"] == "tool"
    assert lines[3]["message"]["toolCallId"] == call["id"]


def test_timestamp_is_exact_epoch_ms() -> None:
    ts = datetime(2026, 3, 1, 10, 0, 0, 123000, tzinfo=UTC)
    # A non-UTC offset must give the same instant.
    ts_off = ts.astimezone(timezone(timedelta(hours=5, minutes=30)))
    for value in (ts, ts_off):
        s = Session(id="s", messages=[Message(id="m", role="user", text="x", timestamp=value)])
        assert session_to_sts_lines(s)[1]["message"]["timestamp"] == 1772359200123
    odd = datetime(2026, 3, 1, 10, 0, 0, 999999, tzinfo=UTC)  # truncates, no float rounding
    s = Session(id="s", messages=[Message(id="m", role="user", text="x", timestamp=odd)])
    assert session_to_sts_lines(s)[1]["message"]["timestamp"] == 1772359200999


def test_minimal_session_omits_optionals() -> None:
    s = Session(
        id="s",
        messages=[
            Message(id="a", role="assistant", tool_calls=[ToolCall(id="t", name="n")]),
            Message(id="b", role="system", text="sys", is_meta=True),
        ],
    )
    lines = session_to_sts_lines(s)
    assert lines[0] == {"type": "session", "harness": "unknown", "id": "s"}
    assert lines[1]["message"] == {
        "role": "assistant",
        "content": "",
        "toolCalls": [{"id": "t", "function": {"name": "n", "arguments": "{}"}}],
    }
    assert lines[2]["message"] == {"role": "system", "content": "sys"}  # meta still written


def test_write_session_redacts_by_default(tmp_path: Path) -> None:
    out = tmp_path / "o.jsonl"
    write_session(fixture_session(), out, anonymizer=UpperAnonymizer())
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert lines[1]["message"]["content"] == "LIST THE FILES"
    assert lines[0]["name"] == "LIST THE DEMO FILES"
    assert out.read_text().endswith("\n")


def test_write_session_no_redact_warns(tmp_path: Path) -> None:
    out = tmp_path / "o.jsonl"
    with pytest.warns(UserWarning, match="Redaction disabled"):
        write_session(fixture_session(), out, redact=False)
    assert [json.loads(x) for x in out.read_text().splitlines()] == golden("tool_session.sts.jsonl")


def test_write_session_unicode_verbatim(tmp_path: Path) -> None:
    out = tmp_path / "o.jsonl"
    s = Session(id="s", messages=[Message(id="m", role="user", text="café")])
    with pytest.warns(UserWarning):
        write_session(s, out, redact=False)
    assert "café" in out.read_text(encoding="utf-8")
