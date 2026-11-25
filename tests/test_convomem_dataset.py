"""Dataset layout, no-clobber commit and topics matching. Offline."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pensieve_traces import Session
from pensieve_traces.builders.convomem import EvidenceItem, EvidenceMessage, dataset
from pensieve_traces.builders.convomem.topics import TopicsError, load_topics, match_topics


def item(category: str, cids: list[str], n: int | None = None) -> EvidenceItem:
    ev = [EvidenceMessage(c, f"{c}-m0", "User", f"text {c}") for c in cids]
    return EvidenceItem(category, n or len(cids), "q?", "a", ev, cids)


CONVS = [
    {"id": c, "containsEvidence": False, "messages": [{"speaker": "User", "text": f"text {c}"}]}
    for c in ("a", "b", "c")
]


def test_layout_folders_and_filler(tmp_path: Path) -> None:
    items = [item("user_facts", ["a"]), item("changing_facts", ["a", "b"])]
    dataset.write_dataset(tmp_path, items, CONVS, {"k": 1})
    base = tmp_path / "core_benchmark/evidence_questions"
    assert (base / "user_evidence/1_evidence/me.json").is_file()
    assert (base / "changing_evidence/2_evidence/me.json").is_file()
    assert sorted(p.name for p in base.iterdir()) == ["changing_evidence", "user_evidence"]
    filler = json.loads((tmp_path / "core_benchmark/filler_conversations/me.json").read_text())
    assert [c["id"] for c in filler["evidence_items"][0]["conversations"]] == ["c"]
    assert json.loads((tmp_path / "build_manifest.json").read_text()) == {"k": 1}


def test_commit_into_absent_and_existing_out(tmp_path: Path) -> None:
    for name, pre in (("absent", False), ("existing", True)):
        tmp = tmp_path / f"{name}.tmp"
        tmp.mkdir()
        dataset.write_dataset(tmp, [item("user_facts", ["a"])], CONVS, {})
        out = tmp_path / name
        if pre:
            out.mkdir()
        dataset.commit_dataset(tmp, out)
        assert (out / "core_benchmark").is_dir() and (out / "build_manifest.json").is_file()


def test_commit_rolls_back_and_keeps_temp_when_rollback_fails(tmp_path: Path, monkeypatch) -> None:
    tmp, out = tmp_path / "t", tmp_path / "out"
    tmp.mkdir()
    out.mkdir()
    dataset.write_dataset(tmp, [item("user_facts", ["a"])], CONVS, {})
    (tmp / "build_manifest.json").unlink()  # the second step fails
    with pytest.raises(dataset.CommitError) as exc:
        dataset.commit_dataset(tmp, out)  # rollback works: core_benchmark/ is back in tmp
    assert exc.value.kept is None
    assert (tmp / "core_benchmark").is_dir() and not (out / "core_benchmark").exists()
    assert not (out / "build_manifest.json").exists()

    real_rename = os.rename

    def rename(src, dst):
        if Path(dst).parent == tmp:
            raise OSError("no")
        real_rename(src, dst)

    monkeypatch.setattr(dataset.os, "rename", rename)
    with pytest.raises(dataset.CommitError) as exc:
        dataset.commit_dataset(tmp, out)  # rollback fails: temp dir kept, path in the message
    assert exc.value.kept == tmp and str(tmp) in str(exc.value)


def write(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data))
    return p


def test_topics_matching(tmp_path: Path) -> None:
    topics = load_topics(
        write(tmp_path, {"x": {"ids": ["s1"]}, "y": {"projects": ["DEMO"], "titles": ["Care"]}})
    )
    assert match_topics(topics, Session(id="s1")) == ["x"]
    assert match_topics(topics, Session(id="s2", project="demo")) == ["y"]
    assert match_topics(topics, Session(id="s3", title="Home CARE plan")) == ["y"]
    assert match_topics(topics, Session(id="s4", title="other")) == []


def test_topics_errors_are_named(tmp_path: Path) -> None:
    with pytest.raises(TopicsError, match="topic 'x': unknown key 'bad'"):
        load_topics(write(tmp_path, {"x": {"bad": []}}))
    with pytest.raises(TopicsError, match="topic 'x': no matchers"):
        load_topics(write(tmp_path, {"x": {"ids": []}}))
    with pytest.raises(TopicsError, match="'ids' must be a list of non-empty strings"):
        load_topics(write(tmp_path, {"x": {"ids": [1]}}))
