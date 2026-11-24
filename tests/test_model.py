"""Canonical model tests: synthetic data built in code, fully offline."""

from datetime import UTC, datetime, timedelta

import pytest

from pensieve_traces import Message, Role, Session, ToolCall, Usage

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def build_session() -> Session:
    msgs = [
        Message(id="m1", role=Role.USER, timestamp=T0, text="list the files"),
        Message(
            id="m2",
            role=Role.ASSISTANT,
            parent_id="m1",
            timestamp=T0 + timedelta(seconds=1),
            text="Running two tools.",
            reasoning="Need ls and cat.",
            tool_calls=[
                ToolCall(id="c1", name="ls", arguments='{"path": "."}'),
                ToolCall(id="c2", name="cat", arguments="not json"),
            ],
            model="model-x",
            request_id="req-1",
            usage=Usage(
                input_tokens=10, output_tokens=5, cache_read_tokens=3, cache_creation_tokens=2
            ),
            metadata={"stop_reason": "tool_use", "extra": [1, {"a": None}]},
        ),
        Message(id="m3", role=Role.TOOL, parent_id="m2", tool_call_id="c1", text="a.txt"),
        Message(
            id="m4",
            role=Role.TOOL,
            parent_id="m2",
            tool_call_id="c2",
            text="boom",
            is_error=True,
        ),
        Message(id="m5", role=Role.ASSISTANT, parent_id="m4", text="sub", is_sidechain=True),
        Message(id="m6", role=Role.SYSTEM, text="reminder", is_meta=True),
    ]
    return Session(
        id="s1",
        harness="test",
        project="proj",
        cwd="/work/proj",
        title="demo",
        started_at=T0,
        ended_at=T0 + timedelta(minutes=1),
        git_branch="main",
        metadata={"k": "v"},
        messages=msgs,
    )


def test_json_round_trip_is_lossless():
    s = build_session()
    again = Session.from_json(s.to_json())
    assert again == s
    assert again.messages[1].metadata == {"stop_reason": "tool_use", "extra": [1, {"a": None}]}
    assert again.messages[3].is_error is True
    assert again.messages[1].timestamp == T0 + timedelta(seconds=1)


def test_defaults_and_order():
    a, b = Message(id="a", role=Role.USER), Message(id="b", role="user")
    assert a.metadata == {} and a.metadata is not b.metadata
    assert a.is_error is False
    assert b.role is Role.USER
    s = build_session()
    assert [m.id for m in Session.from_json(s.to_json()).messages] == [
        "m1", "m2", "m3", "m4", "m5", "m6"
    ]  # fmt: skip


def test_lookups():
    s = build_session()
    assert s.message("m2") is s.messages[1]
    assert s.message("nope") is None
    assert s.parent_of(s.messages[1]) is s.messages[0]
    assert s.parent_of(s.messages[0]) is None
    assert [m.id for m in s.children_of(s.messages[1])] == ["m3", "m4"]
    assert s.tool_result_for("c2") is s.messages[3]
    assert s.tool_result_for("zz") is None
    assert [c.id for c in s.tool_calls()] == ["c1", "c2"]
    assert s.tool_call("c1").name == "ls"
    assert s.tool_call("zz") is None


def test_validation_errors():
    with pytest.raises(ValueError):
        Session(id="s", messages=[Message(id="x", role="user"), Message(id="x", role="user")])
    with pytest.raises(ValueError):
        Message(id="x", role=Role.USER, timestamp=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        Session(id="s", started_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        Message(id="x", role="bogus")
    with pytest.raises(ValueError):
        Message(id="x", role=Role.USER, is_error=True)
    with pytest.raises(ValueError):
        Message(id="x", role=Role.ASSISTANT, tool_call_id="c1")


def test_non_serialisable_metadata_fails_at_to_json():
    s = Session(id="s", metadata={"bad": object()})
    with pytest.raises(TypeError):
        s.to_json()


def test_index_rebuilds_when_message_replaced_at_same_length():
    s = build_session()
    assert s.message("m3") is s.messages[2]
    new = Message(id="n3", role=Role.USER)
    s.messages[2] = new
    assert s.message("m3") is None
    assert s.message("n3") is new


def test_index_rebuild_rejects_duplicate_ids():
    s = build_session()
    s.message("m1")
    s.messages.append(Message(id="m1", role=Role.USER))
    with pytest.raises(ValueError):
        s.message("m1")
