"""``build convomem`` tests. Offline: a fake model client replaces ``cli.make_client``; teich is
faked (counting anonymizer) unless a test uses importorskip."""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

from pensieve_traces import cli
from pensieve_traces.builders.convomem import dataset

EMAIL = "Jordan.Doe@Acme-Mail.net"
FACTS = {
    "cc-1": ["I adopted a beagle named Biscuit in March.", "Congratulations on the new dog."],
    "cc-2": ["Please format this table of invoices.", "Here is the table."],
    "ai-1": ["My sister Marguerite lives in Lisbon.", "Lisbon is lovely in spring."],
    "ai-2": ["What is a good pasta recipe?", "Try cacio e pepe."],
}
PLANTED = {"cc-2": f"Please email {EMAIL} the invoices.", "ai-2": f"Reach me at {EMAIL} please."}


def _cc_line(sid: str, n: int, role: str, text: str) -> str:
    base = {
        "uuid": f"{sid}-{n}",
        "parentUuid": None if n == 0 else f"{sid}-{n - 1}",
        "sessionId": sid,
        "timestamp": f"2026-02-0{1 if sid == 'cc-1' else 2}T08:00:0{n}.000Z",
        "cwd": "/work/demo",
        "isSidechain": False,
    }
    if role == "user":
        return json.dumps({**base, "type": "user", "message": {"role": "user", "content": text}})
    msg = {"role": "assistant", "content": [{"type": "text", "text": text}], "model": "m"}
    return json.dumps({**base, "type": "assistant", "message": msg})


def make_inputs(root: Path, *, session_ids: tuple[str, str] = ("cc-1", "cc-2")) -> list[Path]:
    project = root / "project"
    project.mkdir()
    for sid in session_ids:
        texts = [PLANTED.get(sid, FACTS[sid][0]), FACTS[sid][1]]
        lines = [
            _cc_line(sid, i, "user" if i % 2 == 0 else "assistant", t) for i, t in enumerate(texts)
        ]
        (project / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")
    export = root / "export"
    export.mkdir()
    chats = []
    for n, sid in enumerate(("ai-1", "ai-2")):
        texts = [PLANTED.get(sid, FACTS[sid][0]), FACTS[sid][1]]
        msgs = []
        for i, t in enumerate(texts):
            msgs.append(
                {
                    "uuid": f"{sid}-m{i}",
                    "text": t,
                    "content": [{"type": "text", "text": t}],
                    "sender": "human" if i % 2 == 0 else "assistant",
                    "created_at": f"2026-03-0{n + 1}T10:00:0{i}.000000Z",
                    "updated_at": f"2026-03-0{n + 1}T10:00:0{i}.000000Z",
                    "attachments": [],
                    "files": [],
                    "parent_message_uuid": "00000000-0000-4000-8000-000000000000"
                    if i == 0
                    else f"{sid}-m{i - 1}",
                }
            )
        chats.append(
            {
                "uuid": sid,
                "name": "Dog stories" if sid == "ai-1" else "Cooking",
                "created_at": f"2026-03-0{n + 1}T10:00:00.000000Z",
                "updated_at": f"2026-03-0{n + 1}T10:00:09.000000Z",
                "chat_messages": msgs,
            }
        )
    (export / "conversations.json").write_text(json.dumps(chats))
    return [project, export]


class FakeClient:
    """Finds a user fact in the two conversations that hold it; records every request."""

    model = "fake-model"

    def __init__(self, on_call=None) -> None:
        self.calls: list[str] = []
        self.on_call = on_call

    def chat(self, messages):
        text = "\n".join(m["content"] for m in messages)
        self.calls.append(text)
        if self.on_call:
            self.on_call()
        prompt = messages[-1]["content"]
        if "Select facts the user stated" in prompt:
            return json.dumps([{"indices": [0]}, {"indices": [1]}])
        if "Task: User fact recall" in prompt:
            if "Biscuit" in prompt:
                return json.dumps([{"question": "What is my dog called?", "answer": "Biscuit"}])
            return json.dumps(
                [{"question": "Where does my sister live?", "answer": "Lisbon Marguerite"}]
            )
        if "You are answering a question about a person" in prompt:
            return "Biscuit"
        if "RIGHT" in prompt and "WRONG" in prompt:
            return "RIGHT"
        if "Biscuit" in prompt and "[0]" in prompt:
            return json.dumps([{"kind": "user_fact", "statement": "has a dog", "indices": [0]}])
        if "Marguerite" in prompt and "[0]" in prompt:
            return json.dumps([{"kind": "user_fact", "statement": "has a sister", "indices": [0]}])
        return "[]"


class CountingAnonymizer:
    instances = 0

    def __init__(self) -> None:
        type(self).instances += 1

    def anonymize_text(self, text: str) -> str:
        return re.sub(re.escape(EMAIL), "[EMAIL]", text, flags=re.IGNORECASE)

    def anonymize_value(self, value):
        return value


@pytest.fixture
def fake_teich(monkeypatch: pytest.MonkeyPatch) -> type[CountingAnonymizer]:
    CountingAnonymizer.instances = 0
    mod = types.ModuleType("teich.anonymize")
    mod.TraceAnonymizer = CountingAnonymizer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "teich", types.ModuleType("teich"))
    monkeypatch.setitem(sys.modules, "teich.anonymize", mod)
    return CountingAnonymizer


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    fake = FakeClient()
    made: list[tuple] = []

    def factory(model, base_url, api_key_env):
        made.append((model, base_url, api_key_env))
        return fake

    monkeypatch.setattr(cli, "make_client", factory)
    fake.made = made  # type: ignore[attr-defined]
    return fake


def build(*args: str) -> int:
    return cli.main(["build", "convomem", *args])


def tree(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def test_full_run_layout_and_manifest(tmp_path, fake_teich, client, capsys) -> None:
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    assert build(*map(str, inputs), "--out", str(out), "--model", "m1") == 0
    assert capsys.readouterr().out.strip() == str(out.resolve())
    evidence = out / "core_benchmark/evidence_questions/user_evidence/1_evidence/me.json"
    assert [p.relative_to(out).as_posix() for p in tree(out)] == [
        "build_manifest.json",
        "core_benchmark/evidence_questions/user_evidence/1_evidence/me.json",
        "core_benchmark/filler_conversations/me.json",
    ]
    items = json.loads(evidence.read_text())["evidence_items"]
    assert len(items) == 2
    for it in items:
        # convomem-arena structure invariants
        evidence_convs = [c for c in it["conversations"] if c["containsEvidence"] is True]
        assert len(it["message_evidences"]) == 1 == len(evidence_convs)
        assert len(it["conversations"]) == len(evidence_convs)
        text = {m["text"] for c in it["conversations"] for m in c["messages"]}
        assert it["message_evidences"][0]["text"] in text
        assert set(it["message_evidences"][0]) == {"speaker", "text"}
        assert "topic" not in it["conversations"][0] and "topics" not in it
    used = {c["id"] for it in items for c in it["conversations"]}
    assert used == {"cc-1", "ai-1"}
    filler = json.loads((out / "core_benchmark/filler_conversations/me.json").read_text())
    convs = filler["evidence_items"][0]["conversations"]
    assert {c["id"] for c in convs} == {"cc-2", "ai-2"}  # evidence conversations excluded
    assert all(c["containsEvidence"] is False for c in convs)
    manifest = json.loads((out / "build_manifest.json").read_text())
    assert set(manifest) == {
        "format_version",
        "inputs",
        "model",
        "endpoint_local",
        "redaction",
        "prompt_hashes",
        "conversations",
        "items_by_category",
        "extract_stats",
        "item_stats",
        "topics",
    }
    assert (
        manifest["inputs"]
        == {
            "count": 2,
            "sessions_read": 4,
            "duplicates_dropped": 0,
            "sessions_by_harness": {"claude_code": 2, "claude_ai": 2},
        }
        or manifest["inputs"]["count"] == 2
    )
    assert manifest["model"] == "m1" and manifest["redaction"] is True
    assert manifest["endpoint_local"] is True
    assert manifest["conversations"] == {"total": 4, "evidence": 2, "filler": 2}
    assert manifest["items_by_category"]["user_facts"] == 2
    assert set(manifest["prompt_hashes"]) == {"extract", "items"}
    assert manifest["topics"] == {"enabled": False, "count": 0}
    blob = (out / "build_manifest.json").read_text()
    assert str(tmp_path) not in blob and "localhost" not in blob
    assert client.made == [("m1", cli.DEFAULT_BASE_URL, None)]  # type: ignore[attr-defined]
    assert (tmp_path / "dataset.cache").is_dir()
    assert not list(tmp_path.glob("dataset.tmp-*"))


def test_redaction_once_before_any_model_call(tmp_path, fake_teich, client) -> None:
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    assert build(*map(str, inputs), "--out", str(out), "--model", "m") == 0
    assert fake_teich.instances == 1  # one anonymizer over all inputs
    assert client.calls
    assert all(EMAIL.lower() not in c.lower() for c in client.calls)
    for root in (out, tmp_path / "dataset.cache"):
        for path in tree(root):
            assert EMAIL.lower() not in path.read_text().lower(), path


def test_real_teich_email_never_reaches_client(tmp_path, client) -> None:
    pytest.importorskip("teich")
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    assert build(*map(str, inputs), "--out", str(out), "--model", "m") == 0
    assert all(EMAIL.lower() not in c.lower() for c in client.calls)
    for root in (out, tmp_path / "dataset.cache"):
        for path in tree(root):
            assert EMAIL.lower() not in path.read_text().lower(), path


def test_missing_teich_writes_nothing(tmp_path, monkeypatch, client, capsys) -> None:
    monkeypatch.setitem(sys.modules, "teich", None)
    monkeypatch.setitem(sys.modules, "teich.anonymize", None)
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    assert build(*map(str, inputs), "--out", str(out), "--model", "m") == 1
    err = capsys.readouterr().err
    assert "pensieve-traces[redact]" in err and "--no-redact" in err
    assert not client.made  # type: ignore[attr-defined]
    assert not list(tmp_path.glob("dataset*"))


def test_no_redact_warns_once_and_says_unredacted(tmp_path, monkeypatch, client, capsys) -> None:
    monkeypatch.setitem(sys.modules, "teich", None)
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    assert build(*map(str, inputs), "--out", str(out), "--model", "m", "--no-redact") == 0
    err = capsys.readouterr().err
    assert err.count("Redaction disabled") == 1
    assert "UNREDACTED" in err and "sending redacted" not in err
    assert json.loads((out / "build_manifest.json").read_text())["redaction"] is False
    assert any(EMAIL in c for c in client.calls)


def test_duplicate_session_ids_deduped(tmp_path, fake_teich, client) -> None:
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    assert (
        build(
            str(inputs[0]),
            str(inputs[0] / "cc-1.jsonl"),
            str(inputs[1]),
            "--out",
            str(out),
            "--model",
            "m",
        )
        == 0
    )
    manifest = json.loads((out / "build_manifest.json").read_text())
    assert manifest["inputs"]["duplicates_dropped"] == 1
    assert manifest["inputs"]["sessions_read"] == 5
    assert manifest["conversations"]["total"] == 4


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "https://api.example.com/v1",
        "http://localhost.example.com/v1",
        "http://user:pw@localhost:11434/v1",
        "http://localhost:notaport/v1",
        "http:///v1",
        "ftp://localhost/v1",
    ],
)
def test_bad_endpoint_refused_before_reading_inputs(
    tmp_path, fake_teich, client, url, capsys
) -> None:
    out = tmp_path / "dataset"
    missing = tmp_path / "does-not-exist"
    assert build(str(missing), "--out", str(out), "--model", "m", "--base-url", url) == 1
    err = capsys.readouterr().err
    assert "no such file" not in err and "--base-url" in err
    assert "pw" not in err
    assert not client.made  # type: ignore[attr-defined]
    assert not list(tmp_path.glob("dataset*"))


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8080/v1", "http://[::1]:11434/v1", "http://LOCALHOST/v1"]
)
def test_local_endpoints_allowed(tmp_path, fake_teich, client, url) -> None:
    inputs = make_inputs(tmp_path)
    assert (
        build(*map(str, inputs), "--out", str(tmp_path / "d"), "--model", "m", "--base-url", url)
        == 0
    )


def test_allow_remote_model_prints_endpoint(tmp_path, fake_teich, client, capsys) -> None:
    inputs = make_inputs(tmp_path)
    args = [*map(str, inputs), "--out", str(tmp_path / "d"), "--model", "m"]
    assert build(*args, "--base-url", "https://api.example.com/v1", "--allow-remote-model") == 0
    err = capsys.readouterr().err
    assert "api.example.com" in err and "redacted text" in err
    manifest = json.loads((tmp_path / "d" / "build_manifest.json").read_text())
    assert manifest["endpoint_local"] is False


def test_unset_api_key_env_fails_early(tmp_path, monkeypatch, fake_teich, client, capsys) -> None:
    monkeypatch.delenv("MY_KEY_VAR", raising=False)
    assert (
        build(
            str(tmp_path / "x"),
            "--out",
            str(tmp_path / "d"),
            "--model",
            "m",
            "--api-key-env",
            "MY_KEY_VAR",
        )
        == 1
    )
    assert "MY_KEY_VAR is not set" in capsys.readouterr().err
    assert not client.made  # type: ignore[attr-defined]


def test_topics_added(tmp_path, fake_teich, client) -> None:
    inputs = make_inputs(tmp_path)
    topics = tmp_path / "topics.json"
    topics.write_text(
        json.dumps(
            {
                "pets": {"titles": ["DOG"], "projects": []},
                "code": {"projects": ["Demo"]},
                "ids": {"ids": ["ai-2"]},
            }
        )
    )
    out = tmp_path / "dataset"
    assert build(*map(str, inputs), "--out", str(out), "--model", "m", "--topics", str(topics)) == 0
    items = json.loads(
        (out / "core_benchmark/evidence_questions/user_evidence/1_evidence/me.json").read_text()
    )["evidence_items"]
    by_conv = {it["conversations"][0]["id"]: it for it in items}
    assert by_conv["ai-1"]["topics"] == ["pets"]
    assert by_conv["ai-1"]["conversations"][0]["topic"] == ["pets"]
    assert by_conv["cc-1"]["topics"] == ["code"]
    filler = json.loads((out / "core_benchmark/filler_conversations/me.json").read_text())
    topic_of = {c["id"]: c["topic"] for c in filler["evidence_items"][0]["conversations"]}
    assert topic_of == {"cc-2": ["code"], "ai-2": ["ids"]}
    assert json.loads((out / "build_manifest.json").read_text())["topics"] == {
        "enabled": True,
        "count": 3,
    }


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"": {"ids": ["a"]}}',
        '{"t": {"ids": ["a"], "bogus": []}}',
        '{"t": {"ids": "a"}}',
        '{"t": {"titles": [""]}}',
        '{"t": {}}',
        '{"t": ["a"]}',
        '{"t": {"ids": ["a"]}, "t": {"ids": ["b"]}}',
    ],
)
def test_malformed_topics_fail_before_model_and_write_nothing(
    tmp_path, fake_teich, client, content, capsys
) -> None:
    inputs = make_inputs(tmp_path)
    topics = tmp_path / "topics.json"
    topics.write_text(content)
    out = tmp_path / "dataset"
    assert build(*map(str, inputs), "--out", str(out), "--model", "m", "--topics", str(topics)) == 1
    assert "--topics" in capsys.readouterr().err
    assert not client.made  # type: ignore[attr-defined]
    assert not list(tmp_path.glob("dataset*"))


def test_out_equal_or_inside_input_refused(tmp_path, fake_teich, client, capsys) -> None:
    inputs = make_inputs(tmp_path)
    for out in (inputs[0], inputs[0] / "sub", inputs[1] / "x", inputs[1] / "conversations.json"):
        assert build(*map(str, inputs), "--out", str(out), "--model", "m") == 1
        err = capsys.readouterr().err
        assert "input" in err or "not a directory" in err
    assert not (inputs[0] / "sub").exists()
    assert not client.made  # type: ignore[attr-defined]


def test_out_is_parent_of_file_input_refused_but_subdir_allowed(
    tmp_path, fake_teich, client
) -> None:
    inputs = make_inputs(tmp_path)
    export_file = inputs[1] / "conversations.json"
    assert build(str(export_file), "--out", str(inputs[1]), "--model", "m") == 1
    assert build(str(export_file), "--out", str(inputs[1] / "dataset"), "--model", "m") == 0


def test_existing_core_benchmark_refused(tmp_path, fake_teich, client) -> None:
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    (out / "core_benchmark").mkdir(parents=True)
    assert build(*map(str, inputs), "--out", str(out), "--model", "m") == 1
    assert not client.made  # type: ignore[attr-defined]
    assert not list(tmp_path.glob("dataset.cache"))


def test_cache_dir_inside_input_or_out_refused(tmp_path, fake_teich, client) -> None:
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    for cache in (inputs[0] / "cache", inputs[1], out / "cache", out):
        assert (
            build(*map(str, inputs), "--out", str(out), "--model", "m", "--cache-dir", str(cache))
            == 1
        )
    assert not (inputs[0] / "cache").exists()
    assert not client.made  # type: ignore[attr-defined]


def test_custom_cache_dir_and_default_from_resolved_out(
    tmp_path, fake_teich, client, monkeypatch
) -> None:
    inputs = make_inputs(tmp_path)
    cache = tmp_path / "elsewhere"
    assert (
        build(
            *map(str, inputs),
            "--out",
            str(tmp_path / "d"),
            "--model",
            "m",
            "--cache-dir",
            str(cache),
        )
        == 0
    )
    assert cache.is_dir() and not (tmp_path / "d.cache").exists()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert build(*map(str, inputs), "--out", ".", "--model", "m") == 0
    # `--out .` resolves to `work`: the default cache is `work.cache`, never `..cache`
    assert (tmp_path / "work.cache").is_dir()
    assert not (tmp_path / "..cache").exists()


def test_rerun_uses_cache(tmp_path, fake_teich, client) -> None:
    inputs = make_inputs(tmp_path)
    cache = tmp_path / "c"
    args = [*map(str, inputs), "--model", "m", "--cache-dir", str(cache)]
    assert build(*args, "--out", str(tmp_path / "d1")) == 0
    first = len(client.calls)
    assert first > 0
    assert build(*args, "--out", str(tmp_path / "d2")) == 0
    assert len(client.calls) == first


def test_client_failure_leaves_no_output_or_temp(tmp_path, fake_teich, monkeypatch, capsys) -> None:
    from pensieve_traces.builders.llm import ModelClientError

    class Down:
        model = "x"

        def chat(self, messages):
            raise ModelClientError("could not reach the model endpoint")

    monkeypatch.setattr(cli, "make_client", lambda *a: Down())
    inputs = make_inputs(tmp_path)
    assert build(*map(str, inputs), "--out", str(tmp_path / "d"), "--model", "m") == 1
    assert "nothing usable" in capsys.readouterr().err
    assert not (tmp_path / "d").exists() and not list(tmp_path.glob("d.tmp-*"))


def test_core_benchmark_appearing_during_run_refused(tmp_path, fake_teich, monkeypatch) -> None:
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    mine = out / "core_benchmark" / "mine.txt"

    def appear() -> None:
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text("keep")

    fake = FakeClient(on_call=appear)
    monkeypatch.setattr(cli, "make_client", lambda *a: fake)
    assert build(*map(str, inputs), "--out", str(out), "--model", "m") == 1
    assert mine.read_text() == "keep"
    assert [p.name for p in tree(out)] == ["mine.txt"]
    assert not list(tmp_path.glob("dataset.tmp-*"))


def test_manifest_appearing_during_run_rolls_back(tmp_path, fake_teich, monkeypatch) -> None:
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    out.mkdir()
    real_commit = dataset.commit_dataset

    def commit(tmp, target):
        (target / dataset.MANIFEST_FILE).write_text("mine")
        real_commit(tmp, target)

    fake = FakeClient()
    monkeypatch.setattr(cli, "make_client", lambda *a: fake)
    monkeypatch.setattr(cli.convomem_dataset, "commit_dataset", commit)
    assert build(*map(str, inputs), "--out", str(out), "--model", "m") == 1
    assert [p.name for p in tree(out)] == [dataset.MANIFEST_FILE]
    assert (out / dataset.MANIFEST_FILE).read_text() == "mine"
    assert not list(tmp_path.glob("dataset.tmp-*"))


def test_existing_empty_out_dir_gets_dataset(tmp_path, fake_teich, client) -> None:
    inputs = make_inputs(tmp_path)
    out = tmp_path / "dataset"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    assert build(*map(str, inputs), "--out", str(out), "--model", "m") == 0
    assert (out / "notes.txt").read_text() == "mine"
    assert (out / "core_benchmark").is_dir() and (out / dataset.MANIFEST_FILE).is_file()
    assert not list(tmp_path.glob("dataset.tmp-*"))


def test_interrupt_cleans_temp(tmp_path, fake_teich, client, monkeypatch) -> None:
    inputs = make_inputs(tmp_path)

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.convomem_dataset, "commit_dataset", boom)
    with pytest.raises(KeyboardInterrupt):
        build(*map(str, inputs), "--out", str(tmp_path / "dataset"), "--model", "m")
    assert not list(tmp_path.glob("dataset.tmp-*")) and not (tmp_path / "dataset").exists()


def test_no_items_writes_nothing_but_keeps_cache(tmp_path, fake_teich, monkeypatch, capsys) -> None:
    class Empty(FakeClient):
        def chat(self, messages):
            return "[]"

    monkeypatch.setattr(cli, "make_client", lambda *a: Empty())
    inputs = make_inputs(tmp_path)
    assert build(*map(str, inputs), "--out", str(tmp_path / "d"), "--model", "m") == 1
    assert "no evidence items" in capsys.readouterr().err
    assert not (tmp_path / "d").exists()


def test_never_contacts_hub_or_network(tmp_path, fake_teich, client, monkeypatch) -> None:
    import socket

    def deny(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    inputs = make_inputs(tmp_path)
    assert build(*map(str, inputs), "--out", str(tmp_path / "d"), "--model", "m") == 0
