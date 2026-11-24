"""ConvoMem conversations writer tests. Synthetic fixtures only; offline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pensieve_traces import Message, RedactionUnavailableError, Session, ToolCall
from pensieve_traces.readers.claude_code import read_project
from pensieve_traces.writers.convomem import (
    CONVERSATIONS_FILE,
    MESSAGE_IDS_FILE,
    sessions_to_conversations,
    sessions_to_conversations_with_ids,
    write_conversations,
)

FIX = Path(__file__).parent / "fixtures" / "convomem"


class UpperAnonymizer:
    def __init__(self) -> None:
        self.calls = 0

    def anonymize_text(self, text: str) -> str:
        self.calls += 1
        return text.upper()

    def anonymize_value(self, value):
        if isinstance(value, dict):
            return {k: self.anonymize_value(v) for k, v in value.items()}
        return self.anonymize_text(value) if isinstance(value, str) else value


def sessions() -> list[Session]:
    return read_project(FIX)


# The arena's loaders, reproduced from convomem_arena/data.py (read-only reference).
def parse_conversation(raw: dict) -> dict:
    return {
        "id": str(raw.get("id") or ""),
        "messages": [(m["speaker"], m["text"]) for m in raw["messages"]],
    }


def parse_filler_file(payload: dict) -> list[dict]:
    return [
        parse_conversation(c) for item in payload["evidence_items"] for c in item["conversations"]
    ]


def test_summarize_mode() -> None:
    convs, ids = sessions_to_conversations_with_ids(sessions())
    assert [c["id"] for c in convs] == ["sess-early", "sess-late"]  # ordered by start time
    assert all(c["containsEvidence"] is False for c in convs)
    assert convs[0]["messages"] == [
        {"speaker": "User", "text": "earlier question"},
        {"speaker": "Assistant", "text": "earlier answer"},
    ]
    assert convs[1]["messages"] == [
        {"speaker": "User", "text": "hello there"},
        {"speaker": "Assistant", "text": "Checking the repo.\n[ran Bash: git status]"},
        {"speaker": "Assistant", "text": "[ran Read]"},
        {"speaker": "Assistant", "text": "All clean."},
    ]
    assert ids == {
        "sess-early": ["e1", "e2"],
        "sess-late": ["l1", "l3", "l5", "l7"],
    }


def test_drop_mode() -> None:
    convs, ids = sessions_to_conversations_with_ids(sessions(), tool_activity="drop")
    assert convs[1]["messages"] == [
        {"speaker": "User", "text": "hello there"},
        {"speaker": "Assistant", "text": "Checking the repo."},
        {"speaker": "Assistant", "text": "All clean."},
    ]
    assert ids["sess-late"] == ["l1", "l3", "l7"]


def test_exclusions_and_alignment() -> None:
    for mode in ("summarize", "drop"):
        convs, ids = sessions_to_conversations_with_ids(sessions(), mode)
        blob = json.dumps(convs)
        for hidden in ("meta caveat", "subagent task", "clean tree"):
            assert hidden not in blob
        for c in convs:
            assert len(c["messages"]) == len(ids[c["id"]])
            assert {m["speaker"] for m in c["messages"]} <= {"User", "Assistant"}
        assert len({c["id"] for c in convs}) == len(convs)


def test_sessions_to_conversations_is_sibling_first_element() -> None:
    assert (
        sessions_to_conversations(sessions()) == sessions_to_conversations_with_ids(sessions())[0]
    )


def test_bad_tool_activity() -> None:
    with pytest.raises(ValueError):
        sessions_to_conversations(sessions(), tool_activity="keep")


def test_whitespace_empty_and_no_merge_and_order() -> None:
    t = datetime(2026, 1, 1, tzinfo=UTC)
    undated = Session(
        id="a-undated",
        messages=[Message(id="m1", role="user", text="  \n ")],
    )
    dated = Session(
        id="z-dated",
        started_at=t,
        messages=[
            Message(id="m1", role="assistant", text="one"),
            Message(id="m2", role="assistant", text="two"),
            Message(id="m3", role="system", text="sys"),
        ],
    )
    other = Session(id="b-undated", messages=[Message(id="x", role="user", text="hi")])
    convs, ids = sessions_to_conversations_with_ids([other, undated, dated])
    assert [c["id"] for c in convs] == ["z-dated", "b-undated"]  # undated last, empty omitted
    assert len(convs[0]["messages"]) == 2 and ids["z-dated"] == ["m1", "m2"]


def test_summary_truncated_and_non_object_arguments() -> None:
    msg = Message(
        id="m",
        role="assistant",
        tool_calls=[
            ToolCall("1", "Bash", json.dumps({"command": "echo " + "x" * 200})),
            ToolCall("2", "Bash", "not json"),
            ToolCall("3", "Bash", "[1]"),
        ],
    )
    text = sessions_to_conversations([Session(id="s", messages=[msg])])[0]["messages"][0]["text"]
    first, second, third = text.split("\n")
    assert len(first) <= len("[ran Bash: ]") + 80 and first.endswith("...]")
    assert (second, third) == ("[ran Bash]", "[ran Bash]")


def test_write_default_redacts_with_one_anonymizer(tmp_path: Path) -> None:
    anon = UpperAnonymizer()
    conv_path, ids_path = write_conversations(sessions(), tmp_path, anonymizer=anon)
    assert (conv_path.name, ids_path.name) == (CONVERSATIONS_FILE, MESSAGE_IDS_FILE)
    payload = json.loads(conv_path.read_text())
    loaded = parse_filler_file(payload)
    assert loaded[0]["messages"][0] == ("User", "EARLIER QUESTION")
    # the summary line is built from redacted arguments, so it is redacted too
    assert loaded[1]["messages"][1][1] == "CHECKING THE REPO.\n[ran Bash: GIT STATUS]"
    assert json.loads(ids_path.read_text())["sess-late"] == ["l1", "l3", "l5", "l7"]
    assert anon.calls > 0


def test_write_golden(tmp_path: Path) -> None:
    conv_path, ids_path = write_conversations(sessions(), tmp_path, redact=False)
    assert json.loads(conv_path.read_text()) == json.loads(
        (FIX / "golden.conversations.json").read_text()
    )
    assert json.loads(ids_path.read_text()) == json.loads(
        (FIX / "golden.message_ids.json").read_text()
    )


def test_no_redact_warns(tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="Redaction disabled"):
        write_conversations(sessions(), tmp_path, redact=False)
    assert "hello there" in (tmp_path / CONVERSATIONS_FILE).read_text()


@pytest.mark.parametrize("existing", [CONVERSATIONS_FILE, MESSAGE_IDS_FILE])
def test_existing_target_refused_nothing_written(tmp_path: Path, existing: str) -> None:
    (tmp_path / existing).write_text("keep me")
    with pytest.raises(FileExistsError):
        write_conversations(sessions(), tmp_path, anonymizer=UpperAnonymizer())
    assert (tmp_path / existing).read_text() == "keep me"
    assert len(list(tmp_path.iterdir())) == 1


def test_redaction_unavailable_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "teich", None)
    monkeypatch.setitem(sys.modules, "teich.anonymize", None)
    with pytest.raises(RedactionUnavailableError):
        write_conversations(sessions(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_real_teich_redacts_and_ids_unique(tmp_path: Path) -> None:
    pytest.importorskip("teich")
    src = [
        Session(
            id=f"s{i}",
            started_at=datetime(2026, 1, 1 + i, tzinfo=UTC),
            messages=[Message(id="m1", role="user", text="mail jdoe.private@corp-mail.io please")],
        )
        for i in range(2)
    ]
    conv_path, _ = write_conversations(src, tmp_path)
    text = conv_path.read_text()
    assert "jdoe.private@corp-mail.io" not in text
    convs = json.loads(text)["evidence_items"][0]["conversations"]
    assert len({c["id"] for c in convs}) == 2
