"""Evidence item tests: scripted fake ChatClient, synthetic sessions, offline."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

import pytest

from pensieve_traces import Message, Session
from pensieve_traces.builders.convomem import (
    CATEGORIES,
    Candidate,
    Stats,
    build_items,
    item_prompt_hashes,
)
from pensieve_traces.builders.convomem._text import content_words, specifics
from pensieve_traces.builders.convomem.items import ITEM_PROMPT_FILES, PROMPT_DELIMITER, _load
from pensieve_traces.builders.llm import ModelClientError

_PROMPT_DIR = resources.files("pensieve_traces.builders.convomem") / "prompts"
GROUP = "Propose groups of facts"
GROUPS = {
    "user_facts": "Select facts the user stated",
    "assistant_facts": "Select statements, recommendations",
    "preferences": "Select user preferences",
    "implicit_connections": "Select facts or preferences about the user",
    "abstention": "Select facts that are related",
}
VERIFY = "You are answering a question about a person"
JUDGE = "RIGHT"
IDK = "There is no information in prior conversations to answer this question"
TASKS = {
    "user_facts": "Task: User fact recall",
    "assistant_facts": "Task: Assistant fact recall",
    "changing_facts": "Task: Changing evidence",
    "abstention": "Task: Abstention",
    "preferences": "Task: User preference application",
    "implicit_connections": "Task: Implicit connection",
}


def seq(*replies):
    """A reply script: each call takes the next reply; the last one repeats."""
    items = list(replies)

    def nxt(_prompt: str):
        return items.pop(0) if len(items) > 1 else items[0]

    return nxt


class Router:
    """Answers by what the prompt says. A rule is (needle, reply); first match wins.

    A reply is a str, a JSON-able value, an Exception (raised), or a callable(prompt).
    """

    model = "fake-model"

    def __init__(self, *rules, default="[]") -> None:
        self.rules = list(rules)
        self.default = default
        self.calls: list[str] = []

    def chat(self, messages):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        reply = self.default
        for needle, candidate in self.rules:
            if needle in prompt:
                reply = candidate
                break
        if callable(reply):
            reply = reply(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    def with_needle(self, needle: str) -> list[str]:
        return [c for c in self.calls if needle in c]


class NoCalls:
    model = "fake-model"

    def chat(self, messages):
        raise AssertionError("client must not be called")


def qa(question: str, answer: str) -> list[dict[str, str]]:
    return [{"question": question, "answer": answer}]


def make(sid: str, texts: list[str], harness: str = "claude_code", day: int = 1) -> Session:
    msgs = [
        Message(
            id=f"{sid}-m{i}",
            role="user" if i % 2 == 0 else "assistant",
            text=t,
            timestamp=datetime(2025, 1, day, tzinfo=UTC) + timedelta(seconds=i),
        )
        for i, t in enumerate(texts)
    ]
    return Session(
        id=sid, harness=harness, started_at=datetime(2025, 1, day, tzinfo=UTC), messages=msgs
    )


def cand(session: Session, kind: str, pos: list[int], statement: str = "a fact", **kw) -> Candidate:
    msgs = [session.messages[p] for p in pos]
    return Candidate(
        kind=kind,  # type: ignore[arg-type]
        statement=statement,
        conversation_id=session.id,
        message_ids=[m.id for m in msgs],
        speakers=["User" if m.role == "user" else "Assistant" for m in msgs],
        texts=[m.text for m in msgs],
        timestamps=[m.timestamp for m in msgs],
        positions=pos,
        **kw,
    )


def world():
    """Two Claude Code style sessions, one claude.ai chat, one unrelated filler chat."""
    a = make(
        "a",
        [
            "I adopted a beagle named Biscuit in March.",
            "I recommend the Harborview clinic for vaccines.",
            "I prefer window seats on long flights.",
            "Planning my trip to Lisbon next spring.",
            "My rent is 1000 dollars a month.",
        ],
    )
    b = make("b", ["My rent went up to 1500 dollars a month."], harness="claude.ai", day=2)
    f = make("f", ["Let us talk about gardening tools.", "Sure, a trowel helps."], day=3)
    return a, b, f


def of(items, category):
    return [i for i in items if i.category == category]


def run(cands, sessions, client, **kw):
    return build_items(cands, sessions, client, **kw)


def all_categories_router(groups=None, **over):
    """``groups``: one reply for the ``user_facts`` grouping call (the others return []), or a
    dict category -> reply. Default: every grouping call proposes ``[{"indices": [0]}]``."""
    if groups is None:
        _groups = {}
    elif isinstance(groups, dict):
        _groups = {**{k: [] for k in GROUPS}, **groups}
    else:
        _groups = {**{k: [] for k in GROUPS}, "user_facts": groups}
    rules = [
        (VERIFY, "My answer."),
        *[(n, _groups.get(c, [{"indices": [0]}])) for c, n in GROUPS.items()],
        (TASKS["user_facts"], qa("What is the name of the dog I adopted?", "Biscuit")),
        (TASKS["assistant_facts"], qa("Which clinic did you recommend?", "The Harborview clinic.")),
        (TASKS["changing_facts"], qa("How much is my rent?", "It is 1500 dollars a month.")),
        (TASKS["abstention"], qa("Which hotel will I book?", IDK)),
        (TASKS["preferences"], qa("Where should I sit on a flight?", "Prefers window seats.")),
        (TASKS["implicit_connections"], qa("What should I do today?", "A good response should...")),
        (JUDGE, "RIGHT"),
    ]
    for needle, reply in over.items():
        rules.insert(0, (needle, reply))
    return Router(*rules)


def six_candidates(a, b):
    return [
        cand(a, "user_fact", [0]),
        cand(a, "assistant_fact", [1]),
        cand(a, "preference", [2]),
        cand(a, "changing_value", [4], subject="rent", value="1000"),
        cand(b, "changing_value", [0], subject="Rent", value="1500"),
    ]


def test_all_six_categories() -> None:
    a, b, f = world()
    client = all_categories_router()
    items, st = run(six_candidates(a, b), [a, b, f], client)
    by = {i.category: i for i in items}
    assert [i.category for i in items] == [
        "user_facts",
        "assistant_facts",
        "changing_facts",
        "abstention",
        "preferences",
        "implicit_connections",
    ]
    for item in items:
        assert item.evidence_count in CATEGORIES[item.category][1]
        assert item.evidence_count == len(item.evidence)
    user = by["user_facts"]
    assert user.question.startswith("What is the name") and user.answer == "Biscuit"
    assert user.evidence[0].text == "I adopted a beagle named Biscuit in March."
    assert user.evidence[0].speaker == "User" and user.evidence[0].message_id == "a-m0"
    assert user.conversation_ids == ["a"]
    assert by["abstention"].answer == IDK
    assert st.items == 6 and sum(st.items_by_category.values()) == 6
    assert st.dropped_verification == 0 and st.model_calls == len(client.calls)
    assert Stats().to_dict()["items_by_category"] == dict.fromkeys(CATEGORIES, 0)
    assert user.to_dict()["evidence"][0]["conversation_id"] == "a"


def test_changing_facts_sorted_chronologically_from_out_of_order_input() -> None:
    a, b, f = world()
    later, earlier = (
        cand(b, "changing_value", [0], subject="rent", value="1500"),
        cand(a, "changing_value", [4], subject="rent", value="1000"),
    )
    client = all_categories_router()
    items, _ = run([later, earlier], [a, b, f], client)
    (item,) = items
    assert item.category == "changing_facts" and item.evidence_count == 2
    assert [e.message_id for e in item.evidence] == ["a-m4", "b-m0"]
    prompt = client.with_needle(TASKS["changing_facts"])[0]
    assert prompt.index("1000 dollars") < prompt.index("1500 dollars")
    assert item.conversation_ids == ["a", "b"]


def test_changing_equal_neighbouring_values_are_not_a_change() -> None:
    a, b, f = world()
    same = [
        cand(a, "changing_value", [4], subject="rent", value="1000"),
        cand(b, "changing_value", [0], subject="rent", value="1000"),
    ]
    client = Router()
    items, st = run(same, [a, b, f], client, verify=False)
    assert items == [] and client.calls == [] and st.dropped_chronology == 0


def test_changing_keeps_latest_six() -> None:
    sessions = [make(f"s{i}", [f"The code is {i}00 now."], day=i + 1) for i in range(8)]
    cands = [
        cand(s, "changing_value", [0], subject="code", value=f"{i}00")
        for i, s in enumerate(sessions)
    ]
    client = all_categories_router(
        **{TASKS["changing_facts"]: qa("What is the code?", "It is 700.")}
    )
    items, _ = run(cands[::-1], sessions, client, verify=False)
    assert items[0].evidence_count == 6
    assert [e.conversation_id for e in items[0].evidence] == ["s2", "s3", "s4", "s5", "s6", "s7"]


def test_cross_harness_item_has_two_conversations() -> None:
    a, b, f = world()
    cands = [cand(a, "user_fact", [4]), cand(b, "user_fact", [0])]
    client = all_categories_router(
        groups=[{"indices": [0, 1]}],
        **{TASKS["user_facts"]: qa("How much did my rent rise?", "By 500.")},
    )
    items, _ = run(cands, [a, b, f], client)
    (item,) = items
    assert item.evidence_count == 2 and item.conversation_ids == ["a", "b"]
    assert {a.harness, b.harness} == {"claude_code", "claude.ai"}
    assert [e.conversation_id for e in item.evidence] == ["a", "b"]


# ---- checks: one hand-built failing case each ----------------------------------------


def test_check_evidence_not_verbatim() -> None:
    a, b, f = world()
    edited = cand(a, "user_fact", [0])
    edited.texts = ["I adopted a beagle named Biscuit"]  # a cut message
    unknown = cand(a, "user_fact", [0])
    unknown.message_ids = ["nope"]
    other_speaker = cand(a, "user_fact", [0])
    other_speaker.speakers = ["Assistant"]
    for bad in (edited, unknown, other_speaker):
        client = all_categories_router(groups=[{"indices": [0]}])
        items, st = run([bad], [a, b, f], client)
        assert items == [] and st.dropped_evidence_not_verbatim == 1
        assert not client.with_needle(TASKS["user_facts"])  # no wording call is paid for


def test_check_chronology_missing_timestamp() -> None:
    a, b, f = world()
    undated = cand(b, "changing_value", [0], subject="rent", value="1500")
    undated.timestamps = [None]
    cands = [cand(a, "changing_value", [4], subject="rent", value="1000"), undated]
    client = Router()
    items, st = run(cands, [a, b, f], client)
    assert items == [] and st.dropped_chronology == 1 and client.calls == []


def test_check_changing_answer_must_match_last_value() -> None:
    a, b, f = world()
    client = all_categories_router(
        **{TASKS["changing_facts"]: qa("How much is my rent?", "1000 dollars.")}
    )
    items, st = run(
        [
            cand(a, "changing_value", [4], subject="rent", value="1000"),
            cand(b, "changing_value", [0], subject="rent", value="1500"),
        ],
        [a, b, f],
        client,
    )
    assert items == [] and st.dropped_answer_mismatch == 1


def test_check_duplicate_question() -> None:
    a, b, f = world()
    client = all_categories_router(groups=[{"indices": [0]}, {"indices": [0]}])
    items, st = run([cand(a, "user_fact", [0])], [a, b, f], client, verify=False)
    assert [i.category for i in items] == ["user_facts"]
    assert st.dropped_duplicate_question == 1
    # the same question in a different spelling is a duplicate too
    client = all_categories_router(
        groups=[{"indices": [0]}, {"indices": [1]}],
        **{
            TASKS["user_facts"]: seq(
                qa("What is my dog's name?", "Biscuit"), qa("what is my dog's NAME", "Biscuit")
            ),
        },
    )
    items, st = run(
        [cand(a, "user_fact", [0]), cand(a, "user_fact", [4])], [a, b, f], client, verify=False
    )
    assert len(items) == 1 and st.dropped_duplicate_question == 1


def filler_world():
    a = make(
        "a", ["Her contact is Maya Okafor.", "Noted.", "Planning a trip to Lisbon next spring."]
    )
    f = make(
        "f",
        ["Maya Okafor joined the call about the budget.", "Lisbon hotel book next spring."],
        day=2,
    )
    return a, f


def test_check_answer_terms_in_filler() -> None:
    a, f = filler_world()
    client = all_categories_router(
        **{TASKS["user_facts"]: qa("Who is my contact?", "Your contact is Maya Okafor.")}
    )
    items, st = run([cand(a, "user_fact", [0])], [a, f], client, verify=False)
    assert of(items, "user_facts") == [] and st.dropped_answer_in_filler == 1


def test_numbers_alone_in_filler_do_not_count() -> None:
    a = make("a", ["The deposit was 4500 and the fee 1200."])
    f = make("f", ["Budget lines: 4500, 1200."], day=2)
    client = all_categories_router(
        **{TASKS["user_facts"]: qa("What did I pay?", "It was 4500 and 1200.")}
    )
    items, st = run([cand(a, "user_fact", [0])], [a, f], client, verify=False)
    assert len(of(items, "user_facts")) == 1 and st.dropped_answer_in_filler == 0


def test_filler_ignores_the_items_own_conversations_but_not_other_evidence_conversations() -> None:
    # The only message holding the terms is in a conversation that is itself evidence for another
    # item; it still counts as filler for this item, because the dataset writes it as filler.
    a, f = filler_world()
    client = all_categories_router(
        **{TASKS["user_facts"]: qa("Who is my contact?", "Your contact is Maya Okafor.")}
    )
    items, st = run([cand(f, "user_fact", [0])], [a, f], client, verify=False)
    assert of(items, "user_facts") == [] and st.dropped_answer_in_filler == 1  # "a" is filler here


def test_check_abstention_topic_in_filler() -> None:
    a, f = filler_world()
    client = all_categories_router(
        **{TASKS["abstention"]: qa("Which hotel will I book in Lisbon next spring?", IDK)}
    )
    items, st = run([cand(a, "user_fact", [2])], [a, f], client, verify=False)
    assert [i.category for i in items] == [] or all(i.category != "abstention" for i in items)
    assert st.dropped_topic_in_filler == 1


def test_preferences_and_implicit_connections_have_no_filler_check() -> None:
    a, f = filler_world()
    client = all_categories_router(
        groups={"preferences": [{"indices": [0]}]},
        **{TASKS["preferences"]: qa("Who should I call?", "Maya Okafor, always.")},
    )
    items, st = run([cand(a, "preference", [0])], [a, f], client, verify=False)
    assert "preferences" in {i.category for i in items}
    assert st.dropped_answer_in_filler == 0


def test_check_verification_failure_drops_and_verify_false_skips_it() -> None:
    a, b, f = world()
    cands = [cand(a, "user_fact", [0])]
    wrong = all_categories_router(**{JUDGE: "WRONG"})
    items, st = run(cands, [a, b, f], wrong)
    assert items == [] and st.dropped_verification >= 1
    assert wrong.with_needle(VERIFY)
    off = all_categories_router(**{JUDGE: "WRONG"})
    items, st = run(cands, [a, b, f], off, verify=False)
    assert "user_facts" in {i.category for i in items} and not off.with_needle(VERIFY)
    assert st.dropped_verification == 0


def test_verification_is_on_by_default_and_judge_must_say_right() -> None:
    a, b, f = world()
    for verdict, kept in (
        ("RIGHT", True),
        ("<think>x</think>\nright.", True),
        ("maybe", False),
        ("", False),
    ):
        client = all_categories_router(groups=[{"indices": [0]}], **{JUDGE: verdict})
        items, _ = run([cand(a, "user_fact", [0])], [a, b, f], client)
        assert ("user_facts" in {i.category for i in items}) is kept, verdict


def test_verification_answers_from_the_evidence_conversations_only() -> None:
    a, b, f = world()
    client = all_categories_router(groups=[{"indices": [0]}])
    run([cand(a, "user_fact", [0])], [a, b, f], client)
    prompt = client.with_needle(VERIFY)[0]
    assert "Conversation 1:\nUser: I adopted a beagle" in prompt
    assert (
        "gardening" not in prompt
        and "rent went up" not in prompt
        and "Conversation 2" not in prompt
    )


def test_abstention_passes_when_the_model_abstains() -> None:
    a, b, f = world()
    client = all_categories_router(
        groups={"abstention": [{"indices": [0]}]},
        **{VERIFY: "I don't know, that was never mentioned."},
    )
    items, _ = run([cand(a, "user_fact", [3])], [a, b, f], client)
    assert "abstention" in {i.category for i in items}
    judge_prompts = [c for c in client.calls if "ABSTENTION test" in c]
    assert judge_prompts and "I don't know" in judge_prompts[0]


@pytest.mark.parametrize(
    "category,kind,reply",
    [
        ("preferences", "preference", [{"indices": [0, 1, 2]}]),  # 3 > max 2
        ("user_facts", "user_fact", [{"indices": [7]}]),  # unknown index
        ("user_facts", "user_fact", [{"indices": []}]),
        ("user_facts", "user_fact", [{"indices": [True]}]),
        ("user_facts", "user_fact", ["x"]),
    ],
)
def test_bad_groups_are_dropped_and_counted(category, kind, reply) -> None:
    a, b, f = world()
    cands = [cand(a, kind, [0]), cand(a, kind, [2]), cand(a, kind, [4])]
    client = all_categories_router(groups={category: reply})
    items, st = run(cands, [a, b, f], client)
    assert not [i for i in items if i.category == category] and st.dropped_bad_group >= 1


def test_evidence_count_outside_the_category_range_is_dropped() -> None:
    a, b, f = world()
    wide = cand(a, "preference", [0, 1, 2])  # one candidate, 3 messages > preferences max 2
    client = all_categories_router(groups={"preferences": [{"indices": [0]}]})
    items, st = run([wide], [a, b, f], client, verify=False)
    assert "preferences" not in {i.category for i in items} and st.dropped_bad_group >= 1


def test_groups_never_span_group_chunk() -> None:
    a, b, f = world()
    cands = [
        cand(a, "user_fact", [0], "one"),
        cand(a, "user_fact", [2], "two"),
        cand(a, "user_fact", [4], "three"),
    ]
    client = all_categories_router(groups=[{"indices": [0, 1]}])
    items, _ = run(cands, [a, b, f], client, group_chunk=2, verify=False)
    user_calls = client.with_needle(GROUPS["user_facts"])
    assert len(user_calls) == 2
    assert "two" in user_calls[0] and "three" not in user_calls[0] and "[1]" not in user_calls[1]
    assert user_calls[1].count("three") >= 1
    # the second chunk has one fact, so its reply [0, 1] is out of range
    assert [i.evidence_count for i in items if i.category == "user_facts"] == [2]


def test_group_chunk_must_be_positive() -> None:
    with pytest.raises(ValueError):
        run([], [], NoCalls(), group_chunk=0)


def test_no_candidates_no_calls() -> None:
    a, b, f = world()
    items, st = run([], [a, b, f], NoCalls())
    assert items == [] and st.model_calls == 0 and st.items == 0


def test_replies_are_parsed_tolerantly() -> None:
    a, b, f = world()
    fenced = (
        '<think>hmm</think>\n```json\n{"items": [{"question": "Q one?", "answer": "A one"}]}\n```'
    )
    client = all_categories_router(**{TASKS["user_facts"]: fenced})
    items, _ = run([cand(a, "user_fact", [0])], [a, b, f], client, verify=False)
    assert items[0].question == "Q one?"


@pytest.mark.parametrize(
    "bad", ["not json", "[1]", '[{"question": "q"}]', '[{"question": "", "answer": "a"}]', "[]"]
)
def test_bad_wording_replies_drop_the_group(bad) -> None:
    a, b, f = world()
    client = all_categories_router(**{TASKS["user_facts"]: bad})
    items, st = run([cand(a, "user_fact", [0])], [a, b, f], client, verify=False)
    assert "user_facts" not in {i.category for i in items}
    assert st.dropped_bad_json + st.dropped_missing_fields == 1


def test_failed_calls_are_counted_not_raised() -> None:
    a, b, f = world()
    boom = ModelClientError("HTTP 500: request failed")
    for needle in (GROUP, TASKS["user_facts"]):
        client = all_categories_router(**{needle: boom})
        items, st = run([cand(a, "user_fact", [0])], [a, b, f], client)
        assert "user_facts" not in {i.category for i in items} and st.failed_calls >= 1
    client = all_categories_router(**{VERIFY: boom})
    items, st = run([cand(a, "user_fact", [0])], [a, b, f], client)
    assert "user_facts" not in {i.category for i in items} and st.dropped_verification >= 1


def test_cache_rerun_makes_no_calls(tmp_path: Path) -> None:
    a, b, f = world()
    cands = six_candidates(a, b)
    first = all_categories_router()
    items1, st1 = run(cands, [a, b, f], first, cache_dir=tmp_path / "c")
    assert st1.model_calls > 0 and st1.cache_hits == 0
    items2, st2 = run(cands, [a, b, f], NoCalls(), cache_dir=tmp_path / "c")
    assert st2.model_calls == 0 and st2.cache_hits == st1.model_calls
    assert items2 == items1 and st2.items == st1.items == 6


def test_cache_keeps_a_changed_model_apart(tmp_path: Path) -> None:
    a, b, f = world()
    cands = [cand(a, "user_fact", [0])]
    run(cands, [a, b, f], all_categories_router(), cache_dir=tmp_path)
    other = all_categories_router()
    other.model = "other-model"
    _, st = run(cands, [a, b, f], other, cache_dir=tmp_path)
    assert st.cache_hits == 0 and st.model_calls > 0


def test_cache_path_is_a_file(tmp_path: Path) -> None:
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        run([], [], NoCalls(), cache_dir=f)


def test_candidates_of_one_kind_do_not_leak_into_other_pools() -> None:
    a, b, f = world()
    client = all_categories_router()
    run([cand(a, "assistant_fact", [1], "assistant said X")], [a, b, f], client, verify=False)
    groups = client.with_needle(GROUP)
    assert sum("assistant said X" in g for g in groups) == 2  # assistant_facts and abstention only


# ---- prompts ---------------------------------------------------------------------------


def test_prompt_files_have_header_and_known_placeholders() -> None:
    assert set(item_prompt_hashes()) == set(ITEM_PROMPT_FILES) and len(ITEM_PROMPT_FILES) == 17
    for name in ITEM_PROMPT_FILES:
        raw = (_PROMPT_DIR / name).read_text(encoding="utf-8")
        assert raw.startswith("Copyright (c) 2024 Salesforce, Inc.") and PROMPT_DELIMITER in raw
        body = _load(name)
        assert "Copyright" not in body and "$" not in body and "{goal}" not in body, name
    assert "{statements}" in _load("group_user_facts.txt") and "{max_size}" in _load(
        "group_user_facts.txt"
    )
    assert "{facts}" in _load("items_abstention.txt")
    assert "{conversations}" in _load("verify_answer.txt") and "{question}" in _load(
        "verify_answer.txt"
    )
    for judge in ("default", "rubric", "user_facts_single", "user_facts_multi", "abstention"):
        text = _load(f"judge_{judge}.txt")
        assert "{question}" in text and "{response}" in text
    assert "{evidence}" in _load("judge_user_facts_single.txt") and "{evidence}" in _load(
        "judge_user_facts_multi.txt"
    )
    assert "{answer}" in _load("judge_default.txt") and "{answer}" in _load("judge_rubric.txt")


def test_a_value_that_looks_like_a_placeholder_is_not_expanded() -> None:
    a = make("a", ["Note {answer} and {question} stay as written."])
    client = all_categories_router(groups=[{"indices": [0]}])
    run([cand(a, "user_fact", [0])], [a], client)
    prompt = client.with_needle(VERIFY)[0]
    assert "Note {answer} and {question} stay as written." in prompt
    assert "Question: What is the name of the dog I adopted?" in prompt


# ---- ported filler-check helpers: convomem-arena cases rewritten as synthetic data ---------


def test_specifics_names_and_numbers() -> None:
    answer = "Certainly. Based on your goals, book the Siam Spice table for 4 at 7:30."
    assert set(specifics(answer, "Where should I eat?")) == {"4", "7", "30", "siam", "spice"}
    # sentence-initial words are not names; terms the question already holds are dropped
    assert specifics("Maya works at Globex.", "Who is Maya?") == ["globex"]
    assert specifics("It costs $2,100.", "") == ["2100"]


def test_specifics_hyphenated_and_possessive_names() -> None:
    assert specifics("Call T-Mobile about Maya's plan.", "") == ["mobile", "maya"]
    assert specifics("I've booked it.", "") == []


def test_content_words_drop_stopwords_and_short_tokens() -> None:
    assert content_words("Which hotel did we book in Lisbon?") == ["hotel", "book", "lisbon"]
    assert content_words("Is it a go to 42?") == ["42"]  # two-letter words are not content


@pytest.mark.parametrize(
    "filler_text,dropped",
    [
        ("Maya Okafor, Denver and Globex were mentioned.", True),  # 4 of 4 terms
        ("Maya Okafor and Denver were mentioned.", False),  # 3 of 4 = 75%
    ],
)
def test_filler_threshold_is_80_percent_of_terms_in_one_message(filler_text, dropped) -> None:
    a = make("a", ["Contacts: Maya Okafor in Denver at Globex."])
    answer = "Your contact is Maya Okafor in Denver at Globex."
    terms = specifics(answer, "Who is my contact?")
    assert terms == ["maya", "okafor", "denver", "globex"]
    f = make("f", [filler_text], day=2)
    client = all_categories_router(**{TASKS["user_facts"]: qa("Who is my contact?", answer)})
    items, _ = run([cand(a, "user_fact", [0])], [a, f], client, verify=False)
    kept = "user_facts" in {i.category for i in items}
    assert kept is (not dropped)


def test_filler_terms_split_across_messages_do_not_count() -> None:
    a = make("a", ["Contacts: Maya Okafor in Denver at Globex."])
    f = make("f", ["Maya Okafor", "Denver Globex", "Talk soon."], day=2)
    client = all_categories_router(
        **{
            TASKS["user_facts"]: qa(
                "Who is my contact?", "Your contact is Maya Okafor in Denver at Globex."
            )
        }
    )
    items, _ = run([cand(a, "user_fact", [0])], [a, f], client, verify=False)
    assert "user_facts" in {i.category for i in items}


def test_filler_needs_at_least_two_terms_for_facts_and_three_for_abstention() -> None:
    a = make("a", ["Ask Maya.", "Where is the hotel in Lisbon?"])
    f = make("f", ["Maya said hello.", "Hotel Lisbon question."], day=2)
    one_term = all_categories_router(**{TASKS["user_facts"]: qa("Who?", "Ask Maya.")})
    items, _ = run([cand(a, "user_fact", [0])], [a, f], one_term, verify=False)
    assert "user_facts" in {i.category for i in items}  # one term only: not checked
    two_words = all_categories_router(**{TASKS["abstention"]: qa("Hotel Lisbon?", IDK)})
    items, st = run([cand(a, "user_fact", [1])], [a, f], two_words, verify=False)
    assert "abstention" in {i.category for i in items} and st.dropped_topic_in_filler == 0
