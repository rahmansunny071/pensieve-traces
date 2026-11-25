"""Candidate extraction tests: scripted fake ChatClient, synthetic sessions, offline."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pensieve_traces import Message, Session
from pensieve_traces.builders.convomem import (
    ExtractStats,
    extract_candidates,
    prompt_hashes,
)
from pensieve_traces.builders.convomem.extract import load_prompt
from pensieve_traces.builders.llm import ModelClientError
from pensieve_traces.writers.convomem import sessions_to_conversations_with_ids


class Fake:
    """Replies are consumed in order; an Exception instance is raised."""

    model = "fake-model"

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0) if self.replies else "[]"
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


class NoCalls:
    model = "fake-model"

    def chat(self, messages):
        raise AssertionError("client must not be called")


def make(sid: str, n: int, harness: str = "claude_code", day: int = 1) -> Session:
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(
            Message(
                id=f"{sid}-m{i}",
                role=role,
                text=f"{sid} message {i}",
                timestamp=datetime(2025, 1, day, tzinfo=UTC) + timedelta(seconds=i),
            )
        )
    return Session(
        id=sid, harness=harness, started_at=datetime(2025, 1, day, tzinfo=UTC), messages=msgs
    )


def cand(kind="user_fact", indices=(0,), statement="s", **kw):
    return {
        "kind": kind,
        "statement": statement,
        "subject": None,
        "value": None,
        "indices": list(indices),
        **kw,
    }


def run(sessions, client, **kw):
    stats = ExtractStats()
    return extract_candidates(sessions, client, stats=stats, **kw), stats


def test_all_kinds_verbatim_and_ids() -> None:
    s = make("a", 6)
    reply = [
        cand("user_fact", [0]),
        cand("assistant_fact", [1]),
        cand("preference", [2, 3]),
        cand("changing_value", [4], subject="car budget", value="$25k"),
    ]
    out, st = run([s], Fake(reply))
    convs, _ = sessions_to_conversations_with_ids([s])
    assert [c.kind for c in out] == ["user_fact", "assistant_fact", "preference", "changing_value"]
    pref = out[2]
    assert pref.message_ids == ["a-m2", "a-m3"] and pref.positions == [2, 3]
    assert pref.texts == [m["text"] for m in convs[0]["messages"][2:4]]
    assert pref.speakers == ["User", "Assistant"]
    assert pref.timestamps == [s.messages[2].timestamp, s.messages[3].timestamp]
    assert out[3].subject == "car budget" and out[3].value == "$25k"
    assert out[0].subject is None
    assert st.candidates == 4 and st.windows == 1 and st.model_calls == 1
    assert out[3].__class__.from_dict(out[3].to_dict()) == out[3]


def test_prompt_has_numbered_messages_and_no_attribution_header() -> None:
    fake = Fake([])
    run([make("a", 2)], fake)
    system, user = fake.calls[0]
    assert "[0] User: a message 0" in user["content"] and "[1] Assistant:" in user["content"]
    assert "Salesforce" not in system["content"] + user["content"]
    assert "=====PROMPT=====" not in system["content"]


def test_window_local_indices_and_overlap_dedup() -> None:
    s = make("a", 10)
    # window 6, overlap 2 -> windows start 0 and 4 (4+6 reaches the end)
    first = [cand(indices=[5])]  # global 5
    second = [cand(indices=[1]), cand("preference", [0, 5])]  # global 5 (dup), and 4,9
    out, st = run([s], Fake(first, second), window=6, overlap=2)
    assert st.windows == 2
    assert [c.positions for c in out] == [[5], [4, 9]]
    assert st.merged_duplicates == 1 and st.candidates == 2


def test_dedup_keeps_first_statement() -> None:
    out, _ = run(
        [make("a", 8)],
        Fake([cand(indices=[3], statement="first")], [cand(indices=[0], statement="second")]),
        window=5,
        overlap=2,
    )
    assert [c.statement for c in out] == ["first"]


def test_last_window_reaches_end() -> None:
    fake = Fake()
    run([make("a", 71)], fake)  # 40/10 -> starts 0, 30, 60
    assert len(fake.calls) == 3
    assert "[10] " in fake.calls[2][1]["content"] and "[11] " not in fake.calls[2][1]["content"]


@pytest.mark.parametrize("window,overlap", [(0, 0), (5, 5), (5, -1), (5, 9)])
def test_bad_window_args(window, overlap) -> None:
    with pytest.raises(ValueError):
        extract_candidates([make("a", 3)], NoCalls(), window=window, overlap=overlap)


def test_drops_are_counted() -> None:
    reply = [
        cand("user_fact", [0]),  # ok
        cand("bogus", [0]),  # unknown kind
        cand("user_fact", [99]),  # out of window
        cand("user_fact", [-1]),
        cand("user_fact", []),
        cand("user_fact", ["0"]),
        cand("user_fact", [True]),
        {"kind": "user_fact", "indices": [0]},  # no statement
        cand("changing_value", [0]),  # no subject/value
        "not a dict",
    ]
    out, st = run([make("a", 4)], Fake(reply))
    assert len(out) == 1
    assert st.dropped_unknown_kind == 1
    assert st.dropped_bad_index == 5
    assert st.dropped_missing_fields == 3
    assert st.to_dict()["candidates"] == 1


def test_bad_window_does_not_abort() -> None:
    s = make("a", 10)
    fake = Fake(ModelClientError("HTTP 500"), "garbage", [cand(indices=[0])])
    out, st = run([s], fake, window=4, overlap=0)  # starts 0, 4, 8 -> 3 windows
    assert st.windows == 3 and st.failed_windows == 1 and st.dropped_bad_json == 1
    assert len(out) == 1 and out[0].positions == [8]


@pytest.mark.parametrize(
    "raw,ok",
    [
        ('<think>hmm [1]</think>\n[{"kind":"user_fact","statement":"s","indices":[0]}]', True),
        ('```json\n[{"kind":"user_fact","statement":"s","indices":[0]}]\n```', True),
        ('<think>x</think>```\n[{"kind":"user_fact","statement":"s","indices":[0]}]\n```', True),
        ('{"facts":[{"kind":"user_fact","statement":"s","indices":[0]}]}', True),
        ('{"a":[],"b":[]}', False),
        ('{"facts":"no"}', False),
        ('"just text"', False),
        ("Sure! Here you go: []", False),
        ("", False),
    ],
)
def test_tolerant_parsing(raw, ok) -> None:
    out, st = run([make("a", 2)], Fake(raw))
    assert (len(out) == 1) is ok and st.dropped_bad_json == (0 if ok else 1)


def test_two_harnesses() -> None:
    a, b = make("cc", 4), make("web", 4, harness="claude.ai", day=2)
    out, st = run([a, b], Fake([cand(indices=[0])], [cand(indices=[1])]))
    assert [c.conversation_id for c in out] == ["cc", "web"] and st.windows == 2


def test_cache_second_run_makes_no_calls(tmp_path: Path) -> None:
    s = make("a", 6)
    cache = tmp_path / "cache" / "deep"
    first, st1 = run([s], Fake([cand(indices=[0]), cand("bogus", [0])]), cache_dir=cache)
    assert st1.model_calls == 1 and st1.dropped_unknown_kind == 1
    second, st2 = run([s], NoCalls(), cache_dir=cache)
    assert second == first and st2.model_calls == 0 and st2.cache_hits == 1
    assert st2.dropped_unknown_kind == 1  # revalidated on load, same counts


def test_cache_invalidated_by_model_and_messages(tmp_path: Path) -> None:
    s = make("a", 4)
    run([s], Fake([]), cache_dir=tmp_path)
    other = Fake([])
    other.model = "other"
    _, st = run([s], other, cache_dir=tmp_path)
    assert st.model_calls == 1
    s.messages[0].text = "changed"
    _, st = run([s], Fake([]), cache_dir=tmp_path)
    assert st.model_calls == 1


def test_failed_windows_not_cached(tmp_path: Path) -> None:
    s = make("a", 4)
    run([s], Fake(ModelClientError("x")), cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
    _, st = run([s], Fake("garbage"), cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert st.dropped_bad_json == 1


def test_cache_leaves_other_files_alone(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_bytes(b"keep me")
    run([make("a", 4)], Fake([cand()]), cache_dir=tmp_path)
    assert other.read_bytes() == b"keep me"
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 2 and all(n == "notes.txt" or len(n) == 69 for n in names)
    assert not any(n.startswith(".tmp-") for n in names)


@pytest.mark.parametrize("bad", ["{truncated", '{"version": 1, "reply": "x"}', "[1]", ""])
def test_corrupt_cache_entry_is_recomputed(tmp_path: Path, bad: str) -> None:
    s = make("a", 4)
    run([s], Fake([cand()]), cache_dir=tmp_path)
    (entry,) = tmp_path.iterdir()
    entry.write_text(bad)
    out, st = run([s], Fake([cand()]), cache_dir=tmp_path)
    assert st.model_calls == 1 and st.cache_hits == 0 and len(out) == 1
    out2, st2 = run([s], NoCalls(), cache_dir=tmp_path)
    assert st2.cache_hits == 1 and out2 == out


def test_cache_path_is_a_file(tmp_path: Path) -> None:
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        extract_candidates([make("a", 2)], NoCalls(), cache_dir=f)
    assert f.read_text() == "x"


def test_stats_logged(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="pensieve_traces.builders.convomem.extract"):
        run([make("a", 2)], Fake([cand()]))
    assert '"candidates": 1' in caplog.text


def test_prompts_are_package_resources() -> None:
    for name in ("extract_system.txt", "extract_user.txt"):
        assert load_prompt(name).strip()
    assert "{messages}" in load_prompt("extract_user.txt")
    hashes = prompt_hashes()
    assert set(hashes) == {"extract_system.txt", "extract_user.txt"}
    assert all(len(h) == 64 for h in hashes.values())


def test_prompt_headers_carry_attribution() -> None:
    from importlib import resources

    root = resources.files("pensieve_traces.builders.convomem") / "prompts"
    for name in ("extract_system.txt", "extract_user.txt"):
        head = (root / name).read_text(encoding="utf-8").split("=====PROMPT=====")[0]
        for needle in (
            "Copyright (c) 2024 Salesforce, Inc.",
            "Apache License, Version 2.0",
            "github.com/SalesforceAIResearch/ConvoMem",
            "Modified by pensieve-traces",
        ):
            assert needle in head
    assert "Apache License" in (root / "LICENSE-Apache-2.0.txt").read_text(encoding="utf-8")
