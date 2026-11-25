"""Structure check for generated ConvoMem datasets. Offline, synthetic data."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pensieve_traces.builders.convomem import (
    EvidenceItem,
    EvidenceMessage,
    Problem,
    check_dataset,
    dataset,
)
from pensieve_traces.cli import main

EV = "core_benchmark/evidence_questions"
FILLER = "core_benchmark/filler_conversations/me.json"


def conv(cid: str, *texts: str) -> dict:
    return {
        "id": cid,
        "containsEvidence": False,
        "messages": [{"speaker": "User", "text": t} for t in texts],
    }


CONVS = [conv(c, f"fact {c}", f"other {c}") for c in ("a", "b", "c", "d", "e")]


def item(category: str, cids: list[str], n: int | None = None) -> EvidenceItem:
    ev = [EvidenceMessage(c, f"{c}-0", "User", f"fact {c}") for c in cids]
    return EvidenceItem(category, n or len(cids), "q?", "a", ev, cids)


@pytest.fixture
def ds(tmp_path: Path) -> Path:
    items = [
        item("user_facts", ["a"]),
        item("changing_facts", ["b", "c"]),
        item("abstention", ["d"]),
    ]
    dataset.write_dataset(
        tmp_path,
        items,
        CONVS,
        {},
        topics_by_conversation={"a": ["t"], "b": [], "c": [], "d": [], "e": []},
        topic_order=["t"],
    )
    return tmp_path


def edit(path: Path, fn) -> None:
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


def reasons(problems: list[Problem]) -> list[str]:
    return [p.reason for p in problems]


def test_clean_dataset_passes(ds: Path) -> None:
    assert check_dataset(ds) == []


def test_missing_evidence_text(ds: Path) -> None:
    f = ds / EV / "user_evidence/1_evidence/me.json"
    edit(f, lambda d: d["evidence_items"][0]["message_evidences"][0].update(text="edited"))
    (p,) = check_dataset(ds)
    assert p.item == 0 and p.file.endswith("user_evidence/1_evidence/me.json")
    assert "not a message" in p.reason


def test_count_outside_range(ds: Path) -> None:
    shutil.copytree(ds / EV / "user_evidence/1_evidence", ds / EV / "user_evidence/9_evidence")
    problems = check_dataset(ds)
    assert len(problems) == 1 and "count 9 not allowed" in problems[0].reason


def test_entries_differ_from_folder_count(ds: Path) -> None:
    f = ds / EV / "changing_evidence/2_evidence/me.json"
    edit(f, lambda d: d["evidence_items"][0]["message_evidences"].pop())
    assert any("folder says 2" in r for r in reasons(check_dataset(ds)))


def test_out_of_order_changing_evidence(ds: Path) -> None:
    f = ds / EV / "changing_evidence/2_evidence/me.json"
    edit(f, lambda d: d["evidence_items"][0]["message_evidences"].reverse())
    assert reasons(check_dataset(ds)) == ["changing evidence is not in conversation order"]


def test_evidence_from_one_conversation(ds: Path) -> None:
    f = ds / EV / "changing_evidence/2_evidence/me.json"

    def dup(d: dict) -> None:
        d["evidence_items"][0]["message_evidences"][1] = dict(
            d["evidence_items"][0]["message_evidences"][0]
        )

    edit(f, dup)
    assert reasons(check_dataset(ds)) == ["evidence does not come from distinct conversations"]


def test_evidence_conversation_count_mismatch(ds: Path) -> None:
    f = ds / EV / "user_evidence/1_evidence/me.json"
    edit(f, lambda d: d["evidence_items"][0]["conversations"][0].update(containsEvidence=False))
    assert any("marked containsEvidence" in r for r in reasons(check_dataset(ds)))


def test_evidence_conversation_in_filler(ds: Path) -> None:
    edit(ds / FILLER, lambda d: d["evidence_items"][0]["conversations"].append(conv("a", "x")))
    (p,) = check_dataset(ds)
    assert p.file == FILLER and "in the filler file" in p.reason


def test_unknown_category_folder(ds: Path) -> None:
    (ds / EV / "mystery_evidence/1_evidence").mkdir(parents=True)
    (p,) = check_dataset(ds)
    assert p.reason == "unknown category folder" and p.file.endswith("mystery_evidence")


def test_missing_filler_bad_json_and_no_evidence_dir(ds: Path, tmp_path_factory) -> None:
    (ds / FILLER).unlink()
    assert reasons(check_dataset(ds)) == ["filler file missing for evidence file"]
    (ds / EV / "user_evidence/1_evidence/me.json").write_text("{nope")
    assert "not valid JSON" in reasons(check_dataset(ds))
    empty = tmp_path_factory.mktemp("empty")
    assert reasons(check_dataset(empty)) == ["evidence directory not found"]


def test_empty_question_and_wrong_shape(ds: Path) -> None:
    f = ds / EV / "user_evidence/1_evidence/me.json"
    edit(f, lambda d: d["evidence_items"][0].update(question=" "))
    assert reasons(check_dataset(ds)) == ["question missing or empty"]
    f.write_text("[]")
    assert reasons(check_dataset(ds)) == ['no "evidence_items" list']


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_cli_clean_and_broken(ds: Path, capsys) -> None:
    before = snapshot(ds)
    assert main(["check-convomem", str(ds)]) == 0
    out = capsys.readouterr().out
    assert "user_evidence: 1 files, 1 items" in out and out.rstrip().endswith("ok")
    edit(ds / FILLER, lambda d: d["evidence_items"][0]["conversations"].append(conv("a", "x")))
    after_edit = snapshot(ds)
    with pytest.raises(SystemExit) as exc:
        main(["check-convomem", str(ds)])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert f"{FILLER}: 1 evidence conversation(s) in the filler file" in out
    assert "1 problems" in out
    assert snapshot(ds) == after_edit and before != after_edit


def test_cli_missing_dir(tmp_path: Path, capsys) -> None:
    assert main(["check-convomem", str(tmp_path / "nope")]) == 1
    assert "no such directory" in capsys.readouterr().err
