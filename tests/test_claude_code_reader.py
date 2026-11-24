"""Claude Code reader tests: hand-written synthetic fixtures, fully offline."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pensieve_traces import Role, Usage
from pensieve_traces.readers.claude_code import (
    ClaudeCodeParseError,
    read_project,
    read_session,
)

FIX = Path(__file__).parent / "fixtures" / "claude_code"
LOGGER = "pensieve_traces.readers.claude_code"


def t(sec: int) -> datetime:
    return datetime(2026, 3, 1, 10, 0, sec, tzinfo=UTC)


def seq(session):
    """(id, role, parent_id, text) for every message."""
    return [(m.id, m.role.value, m.parent_id, m.text) for m in session.messages]


def write_lines(tmp_path: Path, *lines: str, name: str = "s.jsonl") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def user_rec(uuid="u1", **kw) -> dict:
    rec = {"type": "user", "uuid": uuid, "parentUuid": None, "message": {"content": "hi"}}
    rec.update(kw)
    return rec


def asst_rec(uuid="a1", usage=None, **kw) -> dict:
    msg = {"content": [{"type": "text", "text": "yo"}]}
    if usage is not None:
        msg["usage"] = usage
    rec = {"type": "assistant", "uuid": uuid, "parentUuid": None, "message": msg}
    rec.update(kw)
    return rec


# -- basic shapes --------------------------------------------------------------------------


def test_plain_chat():
    s = read_session(FIX / "plain_chat.jsonl")
    assert s.id == "sess-plain"
    assert s.harness == "claude_code"
    assert s.cwd == "/work/demo"
    assert s.project == "demo"
    assert s.git_branch == "main"
    assert s.title == "Better greeting"  # last ai-title wins
    assert s.started_at == t(0) and s.ended_at == t(2)
    assert seq(s) == [
        ("u1", "user", None, "hello there"),
        ("a1", "assistant", "u1", "hi, how can I help?"),
        ("u2", "user", "a1", "thanks"),
    ]
    a1 = s.messages[1]
    assert a1.model == "model-x"
    assert a1.request_id == "req-1"
    assert a1.timestamp == t(1)
    assert a1.usage == Usage(input_tokens=10, output_tokens=5)
    assert a1.reasoning is None and a1.tool_calls == []


def test_accepts_str_and_pathlike():
    assert read_session(str(FIX / "plain_chat.jsonl")) == read_session(FIX / "plain_chat.jsonl")


def test_thinking_and_joined_blocks():
    s = read_session(FIX / "thinking.jsonl")
    # The record with only redacted_thinking yields no message.
    assert [m.id for m in s.messages] == ["u1", "a1"]
    a1 = s.messages[1]
    assert a1.reasoning == "first thought\n\nsecond thought"
    assert a1.text == "part one\n\npart two"
    assert a1.usage == Usage(input_tokens=3, output_tokens=4)


def test_tool_use_and_results():
    s = read_session(FIX / "tool_use_result.jsonl")
    assert seq(s) == [
        ("u1", "user", None, "run two commands"),
        ("a1", "assistant", "u1", "Running."),
        ("u2#0", "tool", "a1", "a.txt"),
        ("u2", "tool", "u2#0", "no such file\n\n[image]\n\ngiving up"),
        ("u3", "tool", "u2", ""),  # missing content
    ]
    calls = s.messages[1].tool_calls
    assert [(c.id, c.name) for c in calls] == [("call-1", "Bash"), ("call-2", "Read")]
    assert json.loads(calls[0].arguments) == {"command": "ls"}
    assert json.loads(calls[1].arguments) == {"path": "/work/demo/a.txt"}
    ok, err, empty = s.messages[2:]
    assert (ok.tool_call_id, ok.is_error) == ("call-1", False)
    assert (err.tool_call_id, err.is_error) == ("call-2", True)
    assert empty.tool_call_id == "call-3"
    assert s.tool_result_for("call-2") is err
    assert "SENTINEL" not in s.to_json()


def test_string_content_and_empty_records():
    s = read_session(FIX / "string_content.jsonl")
    # Empty string and unknown-only blocks produce no messages.
    assert seq(s) == [
        ("u1", "user", None, "plain string"),
        ("a1", "assistant", "u1", "string reply"),
    ]


def test_sidechain_and_meta_flags():
    s = read_session(FIX / "sidechain.jsonl")
    assert [(m.id, m.is_sidechain) for m in s.messages] == [
        ("u1", False),
        ("s1", True),
        ("s2", True),
        ("a1", False),
    ]
    m = read_session(FIX / "meta.jsonl")
    assert [(x.id, x.is_meta) for x in m.messages] == [("u1", True), ("u2", False)]


def test_system_records():
    s = read_session(FIX / "system.jsonl")
    assert seq(s) == [
        ("u1", "user", None, "hi"),
        ("sy1", "system", "u1", "Conversation compacted"),
        ("sy3", "system", "sy2", "no subtype"),
    ]
    assert s.messages[1].metadata == {"subtype": "compact_boundary"}
    assert s.messages[2].metadata == {}


def test_image_blocks():
    s = read_session(FIX / "image.jsonl")
    assert [m.text for m in s.messages] == ["look at this\n\n[image]", "[image]"]
    assert "AAAA" not in s.to_json() and "BBBB" not in s.to_json()


def test_empty_file():
    s = read_session(FIX / "empty.jsonl")
    assert s.id == "empty"
    assert s.messages == []
    assert s.started_at is None and s.ended_at is None and s.cwd is None and s.project is None


def test_duplicate_uuid_skipped(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    s = read_session(FIX / "dup_uuid.jsonl")
    assert seq(s) == [("u1", "user", None, "original"), ("a1", "assistant", "u1", "reply")]
    assert any("repeated uuid" in r.getMessage() for r in caplog.records)


# -- filtering -----------------------------------------------------------------------------


def test_internal_records_dropped(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    s = read_session(FIX / "internal_records.jsonl")
    assert seq(s) == [
        ("u1", "user", None, "visible question"),
        ("a1", "assistant", "u1", "visible answer"),
    ]
    assert s.title == "Internal demo"  # non-string aiTitle ignored
    blob = s.to_json()
    assert "SENTINEL" not in blob
    msgs = [r.getMessage() for r in caplog.records]
    for kind in ("file-history-snapshot", "queue-operation", "summary", "progress", "attachment"):
        assert any(repr(kind) in m for m in msgs), kind
    assert any("brand-new-type" in m for m in msgs)
    assert any("ai-title" in m for m in msgs)
    assert not any("SENTINEL" in m for m in msgs)


def test_every_fixture_is_sentinel_free():
    for path in FIX.glob("*.jsonl"):
        if path.name == "blank_truncated.jsonl":
            continue
        assert "SENTINEL" not in read_session(path).to_json(), path.name


def test_ai_title_requires_string(tmp_path):
    p = write_lines(
        tmp_path, json.dumps({"type": "ai-title", "aiTitle": 5}), json.dumps(user_rec())
    )
    assert read_session(p).title is None


# -- errors --------------------------------------------------------------------------------


def test_truncated_line_reports_path_and_line():
    path = FIX / "blank_truncated.jsonl"
    with pytest.raises(ClaudeCodeParseError) as ei:
        read_session(path)
    assert ei.value.path == str(path)
    assert ei.value.line == 3
    assert f"{path}:3" in str(ei.value)
    assert isinstance(ei.value, ValueError)


@pytest.mark.parametrize(
    ("record", "fragment"),
    [
        ("[1, 2]", "not a JSON object"),
        ('"text"', "not a JSON object"),
        (json.dumps(user_rec(timestamp="yesterday")), "bad timestamp"),
        (json.dumps(user_rec(timestamp="2026-03-01T10:00:00")), "timezone-aware"),
        (json.dumps(user_rec(timestamp=12)), "timestamp is not a string"),
        (json.dumps(user_rec(uuid=None)), "uuid"),
        (json.dumps(user_rec(parentUuid=5)), "parentUuid"),
        (json.dumps({"type": "user", "uuid": "u1"}), "no message object"),
        (json.dumps(user_rec(message={"content": 5})), "content"),
        (json.dumps(user_rec(message={"content": ["x"]})), "not an object"),
        (
            json.dumps(user_rec(message={"content": [{"type": "tool_result"}]})),
            "tool_use_id",
        ),
        (
            json.dumps(asst_rec(message={"content": [{"type": "tool_use", "id": 1}]})),
            "tool_use",
        ),
        (json.dumps(asst_rec(usage=[1])), "usage is not an object"),
        (json.dumps(asst_rec(usage={"input_tokens": "5"})), "not an integer"),
        (json.dumps(asst_rec(usage={"output_tokens": 1.5})), "not an integer"),
        (json.dumps(asst_rec(usage={"output_tokens": True})), "not an integer"),
    ],
)
def test_malformed_records_raise_parse_error(tmp_path, record, fragment):
    p = write_lines(tmp_path, json.dumps(user_rec("ok")), record)
    with pytest.raises(ClaudeCodeParseError, match=fragment) as ei:
        read_session(p)
    assert ei.value.line == 2
    assert ei.value.path == str(p)


def test_invalid_utf8_raises_parse_error(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_bytes(b'{"type": "x"}\n\xff\xfe\n')
    with pytest.raises(ClaudeCodeParseError) as ei:
        read_session(p)
    assert ei.value.line == 2


def test_derived_id_collision_raises(tmp_path):
    blocks = [{"type": "tool_result", "tool_use_id": "c"}] * 2
    p = write_lines(
        tmp_path,
        json.dumps(user_rec("x", message={"content": blocks})),
        json.dumps(user_rec("x#0")),
    )
    with pytest.raises(ClaudeCodeParseError, match="collides"):
        read_session(p)


def test_null_timestamp_and_usage_are_allowed(tmp_path):
    p = write_lines(
        tmp_path, json.dumps(user_rec(timestamp=None)), json.dumps(asst_rec(usage=None))
    )
    s = read_session(p)
    assert [m.timestamp for m in s.messages] == [None, None]
    assert s.messages[1].usage is None
    assert s.started_at is None


# -- usage ---------------------------------------------------------------------------------


def test_split_turn_usage_only_on_last_record_of_request():
    s = read_session(FIX / "split_turn.jsonl")
    assert seq(s) == [
        ("u1", "user", None, "go"),
        ("a1", "assistant", "u1", ""),
        ("a2", "assistant", "a1", "doing it"),
        ("a3", "assistant", "a2", ""),
        ("u2", "tool", "a3", "/work/demo"),
        ("a4", "assistant", "u2", "done"),
    ]
    a1, a2, a3, a4 = s.messages[1:4] + s.messages[5:]
    assert {a1.request_id, a2.request_id, a3.request_id} == {"req-1"}
    assert a1.usage is None and a2.usage is None
    # Non-zero cache tokens pin the key mapping; service_tier is ignored.
    assert a3.usage == Usage(
        input_tokens=5, output_tokens=9, cache_read_tokens=100, cache_creation_tokens=20
    )
    assert a4.usage == Usage(input_tokens=2, output_tokens=3, cache_read_tokens=7)
    assert a1.reasoning == "plan" and a3.tool_calls[0].id == "call-1"
    total = sum(m.usage.output_tokens for m in s.messages if m.usage)
    assert total == 9 + 3


def test_missing_usage_keys_default_to_zero(tmp_path):
    p = write_lines(tmp_path, json.dumps(asst_rec(usage={"output_tokens": None, "extra": "x"})))
    assert read_session(p).messages[0].usage == Usage()


def _req_asst(uuid, parent, content, out, rid="req-1"):
    rec = asst_rec(uuid, usage={"output_tokens": out}, requestId=rid, parentUuid=parent)
    rec["message"]["content"] = content
    return json.dumps(rec)


@pytest.mark.parametrize(
    "last_content",
    [
        [{"type": "redacted_thinking", "data": "xx"}],
        [{"type": "mystery_block", "foo": 1}],
    ],
)
def test_usage_of_message_less_last_record_lands_on_last_message(tmp_path, last_content):
    p = write_lines(
        tmp_path,
        _req_asst("a1", None, [{"type": "text", "text": "one"}], 1),
        _req_asst("a2", "a1", [{"type": "text", "text": "two"}], 2),
        _req_asst("a3", "a2", last_content, 9),
    )
    s = read_session(p)
    assert [m.id for m in s.messages] == ["a1", "a2"]
    assert s.messages[0].usage is None
    assert s.messages[1].usage == Usage(output_tokens=9)


def test_usage_dropped_when_group_has_no_messages(tmp_path, caplog):
    p = write_lines(
        tmp_path,
        _req_asst("a1", None, [{"type": "redacted_thinking", "data": "xx"}], 4),
        json.dumps(user_rec("u1")),
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        s = read_session(p)
    assert [m.id for m in s.messages] == ["u1"]
    assert "dropping usage" in caplog.text


@pytest.mark.parametrize("rtype", [["user"], {"a": 1}])
def test_non_string_record_type_is_skipped(tmp_path, caplog, rtype):
    p = write_lines(tmp_path, json.dumps({"type": rtype}), json.dumps(user_rec("u1")))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        s = read_session(p)
    assert [m.id for m in s.messages] == ["u1"]
    assert "skipping record of type" in caplog.text


def test_user_record_with_text_and_two_tool_results(tmp_path):
    rec = user_rec("u2", parentUuid="a1")
    rec["message"]["content"] = [
        {"type": "text", "text": "note"},
        {"type": "tool_result", "tool_use_id": "c1", "content": "r1"},
        {"type": "tool_result", "tool_use_id": "c2", "content": "r2"},
    ]
    s = read_session(write_lines(tmp_path, json.dumps(rec)))
    assert seq(s) == [
        ("u2#0", "user", "a1", "note"),
        ("u2#1", "tool", "u2#0", "r1"),
        ("u2", "tool", "u2#1", "r2"),
    ]
    assert [m.tool_call_id for m in s.messages] == [None, "c1", "c2"]


def test_cross_file_repeated_record_skips_all_derived_messages(tmp_path):
    first = user_rec("u1", sessionId="S")
    first["timestamp"] = "2026-03-01T10:00:00Z"
    again = user_rec("u1", sessionId="S")
    again["timestamp"] = "2026-03-01T10:00:00Z"
    again["message"]["content"] = [
        {"type": "text", "text": "later copy"},
        {"type": "tool_result", "tool_use_id": "c1", "content": "r1"},
    ]
    write_lines(tmp_path, json.dumps(first), name="a.jsonl")
    write_lines(tmp_path, json.dumps(again), name="b.jsonl")
    (s,) = read_project(tmp_path)
    assert [(m.id, m.text) for m in s.messages] == [("u1", "hi")]


# -- read_project --------------------------------------------------------------------------

PROJECT = FIX / "project"


def test_read_project_sessions_and_order():
    sessions = read_project(PROJECT)
    assert [s.id for s in sessions] == ["sess-p0", "sess-p1", "sess-q", "sess-z"]
    keys = [(s.started_at is None, s.started_at, s.id) for s in sessions]
    assert keys == sorted(keys)
    assert sessions[-1].started_at is None and sessions[-1].messages == []


def test_read_project_merges_subagent_files():
    p1 = {s.id: s for s in read_project(PROJECT)}["sess-p1"]
    ids = [m.id for m in p1.messages]
    assert len(ids) == len(set(ids))
    # Equal timestamps keep file order (agent-a1 before sess-p1); untimestamped last.
    assert ids == ["p1-u1", "a1-1", "a1-2", "p1-a1", "p1-u2", "a1-3"]
    assert {m.id: m.is_sidechain for m in p1.messages} == {
        "p1-u1": False,
        "a1-1": True,
        "a1-2": True,
        "p1-a1": False,
        "p1-u2": False,
        "a1-3": True,
    }
    # Cross-file duplicate uuid skipped: the first file's version is kept.
    assert next(m for m in p1.messages if m.id == "a1-1").text == "sub reply"
    # Session fields come from the main file (stem == sessionId), not agent-a1.
    assert (p1.cwd, p1.project, p1.git_branch, p1.title) == (
        "/work/demo",
        "demo",
        "main",
        "Main title",
    )
    assert p1.started_at == t(0)
    assert p1.ended_at == t(9)  # recomputed over both files


def test_read_project_without_main_file_uses_first_sorted():
    q = {s.id: s for s in read_project(PROJECT)}["sess-q"]
    assert [m.id for m in q.messages] == ["b1-u1", "b2-u1"]
    assert (q.cwd, q.project, q.git_branch, q.title) == ("/work/qb1", "qb1", "qb1", "Title B1")
    assert q.started_at == datetime(2026, 3, 2, 9, 0, 0, tzinfo=UTC)
    assert q.ended_at == datetime(2026, 3, 2, 9, 0, 1, tzinfo=UTC)


def test_read_project_single_file_matches_read_session():
    by_id = {s.id: s for s in read_project(PROJECT)}
    assert by_id["sess-p0"] == read_session(PROJECT / "sess-p0.jsonl")


def test_read_project_single_file_keeps_file_order(tmp_path):
    recs = [
        user_rec("late", timestamp="2026-03-01T10:00:09.000Z"),
        user_rec("early", timestamp="2026-03-01T10:00:01.000Z"),
    ]
    write_lines(tmp_path, *(json.dumps(r) for r in recs), name="only.jsonl")
    assert [m.id for m in read_project(tmp_path)[0].messages] == ["late", "early"]
    assert [m.id for m in read_session(tmp_path / "only.jsonl").messages] == ["late", "early"]


def test_read_project_is_not_recursive_and_ignores_other_files(tmp_path):
    write_lines(tmp_path, json.dumps(user_rec("a")), name="one.jsonl")
    (tmp_path / "notes.txt").write_text("not a session")
    sub = tmp_path / "sub"
    sub.mkdir()
    write_lines(sub, "{broken", name="deep.jsonl")
    assert [s.id for s in read_project(tmp_path)] == ["one"]


def test_read_project_propagates_errors_with_path_and_line(tmp_path):
    write_lines(tmp_path, json.dumps(user_rec("a")), name="good.jsonl")
    bad = write_lines(tmp_path, json.dumps(user_rec("b")), "{broken", name="zbad.jsonl")
    with pytest.raises(ClaudeCodeParseError) as ei:
        read_project(tmp_path)
    assert (ei.value.path, ei.value.line) == (str(bad), 2)


def test_read_project_ties_on_started_at_sort_by_id(tmp_path):
    for name in ("b", "a"):
        rec = user_rec(name, timestamp="2026-03-01T10:00:00.000Z", sessionId=name)
        write_lines(tmp_path, json.dumps(rec), name=f"{name}.jsonl")
    assert [s.id for s in read_project(tmp_path)] == ["a", "b"]


# -- teich cross-check ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["plain_chat", "thinking", "tool_use_result", "split_turn"])
def test_matches_teich_when_available(name):
    teich = pytest.importorskip("teich")  # optional cross-check; not a project dependency
    path = FIX / f"{name}.jsonl"

    # Ours: keep user/assistant only, merging assistant messages that share a request_id
    # (teich folds one API response, split over several records, into one message).
    ours: list[tuple[str, str]] = []
    last_request = None
    for m in read_session(path).messages:
        if m.role not in (Role.USER, Role.ASSISTANT):
            continue
        if m.role is Role.ASSISTANT and m.request_id == last_request and ours[-1][0] == "assistant":
            ours[-1] = ("assistant", "\n\n".join(x for x in (ours[-1][1], m.text) if x))
        else:
            ours.append((m.role.value, m.text))
        last_request = m.request_id if m.role is Role.ASSISTANT else None

    example = teich.convert_trace_to_training_example(path)
    theirs = [
        (m["role"], m.get("content") or "")
        for m in example.messages
        if m["role"] in ("user", "assistant")
    ]
    assert [r for r, _ in ours] == [r for r, _ in theirs]
    # Roles must match exactly. Text is compared with whitespace normalised, because teich
    # joins multiple text blocks with "\n" while this reader uses "\n\n" (per the design). If
    # teich ever rewrites text itself, fall back to the roles-only comparison above.
    assert [(r, " ".join(x.split())) for r, x in ours] == [
        (r, " ".join(x.split())) for r, x in theirs
    ]
