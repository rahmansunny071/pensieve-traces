"""claude.ai export reader tests: hand-written synthetic fixtures, fully offline."""

import json
import shutil
from pathlib import Path

import pytest

from pensieve_traces import Role
from pensieve_traces.readers.claude_ai import HARNESS, ClaudeAiParseError, read_export
from pensieve_traces.writers.convomem import sessions_to_conversations
from pensieve_traces.writers.sts import session_to_sts_lines

FIX = Path(__file__).parent / "fixtures" / "claude_ai" / "export"
ROOT = "00000000-0000-4000-8000-000000000000"
SENTINELS = ["ACCOUNT-SENTINEL", "MANIFEST-SENTINEL", "MEMORY-SENTINEL", "SECRET-SUMMARY"]


def by_id() -> dict:
    return {s.id: s for s in read_export(FIX)}


def seq(session):
    return [(m.id, m.role.value, m.parent_id, m.text) for m in session.messages]


def write_export(tmp_path: Path, data) -> Path:
    path = tmp_path / "conversations.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def msg(uuid="m1", parent=ROOT, sender="human", created="2026-03-01T10:00:00Z", **kw):
    d = {
        "uuid": uuid,
        "sender": sender,
        "created_at": created,
        "parent_message_uuid": parent,
        "content": [{"type": "text", "text": "x"}],
    }
    d.update(kw)
    return d


def conv(*messages, uuid="c1", **kw):
    d = {"uuid": uuid, "name": "n", "chat_messages": list(messages)}
    d.update(kw)
    return [d]


def test_directory_and_file_forms_are_equal_and_ordered():
    sessions = read_export(FIX)
    assert sessions == read_export(FIX / "conversations.json")
    assert [s.id for s in sessions] == [
        "conv-plain",
        "conv-tools",
        "conv-branch",
        "conv-attach",
        "conv-empty",
    ]


def test_nothing_private_reaches_the_output():
    blob = "".join(s.to_json() for s in read_export(FIX))
    for sentinel in SENTINELS:
        assert sentinel not in blob
    assert "ATTACHMENT-BODY" not in blob
    assert "ignored top-level text" not in blob


def test_plain_chat():
    s = by_id()["conv-plain"]
    assert (s.harness, HARNESS, s.title) == ("claude.ai", "claude.ai", "Plain chat")
    assert s.started_at.isoformat() == "2026-03-01T10:00:00+00:00"
    assert s.ended_at.isoformat() == "2026-03-01T10:05:00+00:00"
    assert seq(s) == [
        ("p1", "user", None, "Hello there"),
        ("p2", "assistant", "p1", "Hi, how can I help?"),
    ]
    assert s.metadata == {"dropped_branch_messages": 0}


def test_timestamps_are_timezone_aware():
    for s in read_export(FIX):
        for ts in (s.started_at, s.ended_at, *(m.timestamp for m in s.messages)):
            assert ts is not None and ts.utcoffset() is not None


def test_interleaved_assistant_turn_ids_and_parents():
    s = by_id()["conv-tools"]
    assert [(m.id, m.role.value, m.parent_id) for m in s.messages] == [
        ("t1", "user", None),
        ("t2#0", "assistant", "t1"),
        ("t2#1", "tool", "t2#0"),
        ("t2#2", "assistant", "t2#1"),
        ("t2#3", "tool", "t2#2"),
        ("t2", "assistant", "t2#3"),
        ("t3", "user", "t2"),  # parented to the LAST derived message
    ]
    first, tool1, second, tool2, last = s.messages[1:6]
    assert first.text == "Searching." and first.reasoning == "plan it"
    assert [(c.id, c.name, c.arguments) for c in first.tool_calls] == [
        ("tu1", "web_search", '{"query": "cats"}')
    ]
    assert tool1.tool_call_id == "tu1" and not tool1.is_error
    assert tool1.text == "[web results: Cats A, Cats B]"
    assert second.reasoning == "summarise" and second.tool_calls[0].arguments == "{}"
    assert tool2.is_error and tool2.role is Role.TOOL
    assert tool2.text == "boom\n[images: 2]\n[local resource: notes.md]\n[unknown content: mystery]"
    assert last.text == "Done.\n\n[unknown block: brand_new]"
    assert s.tool_result_for("tu2") is tool2


def test_branch_keeps_latest_leaf_path_and_counts_dropped():
    s = by_id()["conv-branch"]
    assert seq(s) == [
        ("b3", "user", None, "Edited root"),
        ("b4", "assistant", "b3", "New answer"),
        ("b5", "user", "b4", "Follow up"),
    ]
    assert s.metadata["dropped_branch_messages"] == 3
    assert s.title is None  # empty name


def test_injected_block_attachments_and_placeholders():
    s = by_id()["conv-attach"]
    meta, user, reply = s.messages
    assert (meta.id, meta.is_meta, meta.text, meta.parent_id) == (
        "a1#0",
        True,
        "Today is a day.",
        None,
    )
    assert (user.id, user.is_meta, user.parent_id) == ("a1", False, "a1#0")
    assert (
        user.text
        == "Read this\n\n[attached: Spec.pdf]\n\n[image]\n\n[attached: pic.png]\n\n[attached: spec.txt]"
    )
    assert reply.parent_id == "a1"


def test_include_attachments_inlines_text():
    s = {x.id: x for x in read_export(FIX, include_attachments=True)}["conv-attach"]
    assert s.messages[1].text.endswith("[attached: spec.txt]\n\nATTACHMENT-BODY")


def test_empty_conversation():
    s = by_id()["conv-empty"]
    assert s.messages == [] and s.metadata == {"dropped_branch_messages": 0}


def test_empty_message_is_skipped_and_child_links_to_ancestor(tmp_path):
    data = conv(
        msg("a", ROOT),
        msg("b", "a", "assistant", content=[]),
        msg("c", "b", created="2026-03-01T10:00:05Z"),
    )
    s = read_export(write_export(tmp_path, data))[0]
    assert [(m.id, m.parent_id) for m in s.messages] == [("a", None), ("c", "a")]


def test_missing_parent_is_a_root(tmp_path):
    s = read_export(write_export(tmp_path, conv(msg("a", "ghost"))))[0]
    assert s.messages[0].parent_id is None


def test_null_fields_are_tolerated(tmp_path):
    blocks = [
        {"type": "thinking", "thinking": None},
        {"type": "tool_use", "id": "t", "name": "n", "input": {}},
        {"type": "tool_result", "tool_use_id": "t", "is_error": None, "content": []},
        {"type": "thinking", "thinking": "late"},
    ]
    s = read_export(write_export(tmp_path, conv(msg(sender="assistant", content=blocks))))[0]
    first, tool, last = s.messages
    assert first.reasoning is None and not tool.is_error and last.reasoning == "late"


def test_wrong_sender_block_is_placeholder(tmp_path):
    blocks = [{"type": "thinking", "thinking": "hmm"}]
    s = read_export(write_export(tmp_path, conv(msg(sender="human", content=blocks))))[0]
    assert s.messages[0].text == "[unknown block: thinking]"


def test_output_flows_through_writers():
    sessions = read_export(FIX)
    for s in sessions:
        session_to_sts_lines(s)
    assert sessions_to_conversations(sessions)


@pytest.mark.parametrize(
    ("data", "needle", "location"),
    [
        ("not json", "invalid JSON", "file"),
        ({"a": 1}, "not a JSON array", "file"),
        ([1], "not an object", "conversations[0]"),
        ([{"chat_messages": []}], "no string uuid", "conversations[0]"),
        ([{"uuid": "c"}], "chat_messages", "conversations[0]"),
        (conv(msg(sender="robot")), "unknown sender", "conversations[0].chat_messages[0]"),
        (  # an unknown sender on a dropped branch still raises
            conv(
                msg("a"),
                msg("b", "a", sender="robot", created="2026-03-01T10:01:00Z"),
                msg("c", "a", sender="assistant", created="2026-03-01T10:02:00Z"),
            ),
            "unknown sender",
            "conversations[0].chat_messages[1]",
        ),
        (conv(msg(created="2026-03-01T10:00:00")), "timezone-aware", "chat_messages[0].created_at"),
        (conv(msg(created="nope")), "bad timestamp", "chat_messages[0].created_at"),
        (conv({"uuid": "m", "sender": "human"}), "missing timestamp", "chat_messages[0]"),
        (conv(msg("a"), msg("a")), "repeated message uuid", "chat_messages[1]"),
        (conv(msg("a", "a")), "cycle", "chat_messages[0]"),
        (conv(msg("a", "b"), msg("b", "a")), "cycle", "chat_messages"),
        (
            conv(msg("a"), uuid="c") + conv(msg("b"), uuid="c"),
            "repeated conversation",
            "conversations[1]",
        ),
        (
            conv(msg(sender="assistant", content=[{"type": "tool_use", "id": 1, "name": "n"}])),
            "tool_use",
            "chat_messages[0]",
        ),
        (
            conv(msg(sender="assistant", content=[{"type": "tool_result", "content": []}])),
            "tool_use_id",
            "chat_messages[0]",
        ),
    ],
)
def test_parse_errors_name_file_and_location(tmp_path, data, needle, location):
    path = write_export(tmp_path, data)
    with pytest.raises(ClaudeAiParseError) as info:
        read_export(path)
    err = info.value
    assert err.path == str(path) and location in err.location
    assert needle in str(err) and isinstance(err, ValueError)


def test_invalid_utf8(tmp_path):
    path = tmp_path / "conversations.json"
    path.write_bytes(b"[\xff]")
    with pytest.raises(ClaudeAiParseError, match="UTF-8"):
        read_export(path)


def test_directory_without_conversations_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_export(tmp_path)


def test_directory_form_copy_is_equal(tmp_path):
    shutil.copytree(FIX, tmp_path / "x")
    assert read_export(tmp_path / "x") == read_export(FIX)
