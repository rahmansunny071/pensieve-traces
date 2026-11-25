"""CLI tests. Offline; teich is faked unless a test uses importorskip."""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
import types
from pathlib import Path

import pytest

from pensieve_traces import cli

FIX = Path(__file__).parent / "fixtures"
SESSION = FIX / "sts" / "tool_session.jsonl"
PROJECT = FIX / "claude_code" / "project"


class _FakeAnonymizer:
    def anonymize_text(self, text: str) -> str:
        return text.upper()

    def anonymize_value(self, value):
        return value


@pytest.fixture
def fake_teich(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("teich.anonymize")
    mod.TraceAnonymizer = _FakeAnonymizer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "teich", types.ModuleType("teich"))
    monkeypatch.setitem(sys.modules, "teich.anonymize", mod)


@pytest.fixture
def no_teich(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "teich", None)
    monkeypatch.setitem(sys.modules, "teich.anonymize", None)


def run(*args: str) -> int:
    return cli.main(["convert", *args])


def test_convert_file_redacts_by_default(tmp_path: Path, fake_teich: None) -> None:
    assert run(str(SESSION), "--to", "sts", "--out", str(tmp_path / "out")) == 0
    lines = (tmp_path / "out" / "sess-sts.jsonl").read_text().splitlines()
    assert json.loads(lines[1])["message"]["content"] == "LIST THE FILES"
    assert all("subagent task" not in x for x in lines)


def test_convert_include_sidechains(tmp_path: Path, fake_teich: None) -> None:
    assert run(str(SESSION), "--to", "sts", "--out", str(tmp_path), "--include-sidechains") == 0
    assert "SUBAGENT TASK" in (tmp_path / "sess-sts.jsonl").read_text()


def test_convert_directory_one_file_per_session(tmp_path: Path, fake_teich: None) -> None:
    assert run(str(PROJECT), "--to", "sts", "--out", str(tmp_path)) == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["sess-p0.jsonl", "sess-p1.jsonl", "sess-q.jsonl", "sess-z.jsonl"]
    for p in tmp_path.iterdir():
        assert json.loads(p.read_text().splitlines()[0])["type"] == "session"


def test_missing_teich_fails_cleanly(
    tmp_path: Path, no_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert run(str(PROJECT), "--to", "sts", "--out", str(out)) == 1
    err = capsys.readouterr().err
    assert "pensieve-traces[redact]" in err and "--no-redact" in err
    assert "Traceback" not in err
    assert list(out.iterdir()) == []


def test_no_redact_writes_raw_and_warns(
    tmp_path: Path, no_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(str(SESSION), "--to", "sts", "--out", str(tmp_path), "--no-redact") == 0
    assert "Redaction disabled" in capsys.readouterr().err
    first = json.loads((tmp_path / "sess-sts.jsonl").read_text().splitlines()[1])
    assert first["message"]["content"] == "list the files"


def test_bad_session_id_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "in"
    src.mkdir()
    shutil.copy(SESSION, src / "ok.jsonl")
    bad = SESSION.read_text().replace("sess-sts", "../evil")
    (src / "bad.jsonl").write_text(bad)
    out = tmp_path / "out"
    assert run(str(src), "--to", "sts", "--out", str(out), "--no-redact") == 1
    assert "cannot be used as a file name" in capsys.readouterr().err
    assert not out.exists() or list(out.iterdir()) == []
    assert not (tmp_path / "evil.jsonl").exists()


@pytest.mark.parametrize("out", [".", "sub"])
def test_out_in_input_dir_refused(
    tmp_path: Path, out: str, fake_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    src = project / "sess-sts.jsonl"  # named <session id>.jsonl, as Claude Code does
    shutil.copy(SESSION, src)
    original = src.read_bytes()
    assert run(str(project), "--to", "sts", "--out", str(project / out)) == 1
    assert src.read_bytes() == original
    assert sorted(p.name for p in project.iterdir()) == ["sess-sts.jsonl"]
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "not overwritten" in err


def test_out_is_input_file_dir_refused(
    tmp_path: Path, fake_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "sess-sts.jsonl"
    shutil.copy(SESSION, src)
    original = src.read_bytes()
    assert run(str(src), "--to", "sts", "--out", str(tmp_path)) == 1
    assert src.read_bytes() == original
    assert "not overwritten" in capsys.readouterr().err


def test_out_beside_input_file_subdir_allowed(tmp_path: Path, fake_teich: None) -> None:
    src = tmp_path / "sess-sts.jsonl"
    shutil.copy(SESSION, src)
    assert run(str(src), "--to", "sts", "--out", str(tmp_path / "traces")) == 0
    assert (tmp_path / "traces" / "sess-sts.jsonl").is_file()


def test_missing_path_and_empty_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(str(tmp_path / "nope"), "--to", "sts", "--out", str(tmp_path / "o")) == 1
    assert "no such file" in capsys.readouterr().err
    (tmp_path / "empty").mkdir()
    assert run(str(tmp_path / "empty"), "--to", "sts", "--out", str(tmp_path / "o")) == 1
    assert "no *.jsonl" in capsys.readouterr().err


def test_parse_error_is_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n")
    assert run(str(bad), "--to", "sts", "--out", str(tmp_path / "o")) == 1
    err = capsys.readouterr().err
    assert err.startswith("pensieve-traces: error:") and "Traceback" not in err


def test_unknown_target_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        run(str(SESSION), "--to", "nope", "--out", str(tmp_path))
    assert exc.value.code == 2


def test_script_entry_point_declared() -> None:
    data = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert data["project"]["scripts"]["pensieve-traces"] == "pensieve_traces.cli:main"
    assert callable(cli.main)


def test_real_teich_redacts(tmp_path: Path) -> None:
    pytest.importorskip("teich")
    src = tmp_path / "s.jsonl"
    text = SESSION.read_text().replace("list the files", "mail jdoe.private@corp-mail.io please")
    src.write_text(text)
    assert run(str(src), "--to", "sts", "--out", str(tmp_path / "o")) == 0
    out = (tmp_path / "o" / "sess-sts.jsonl").read_text()
    assert "jdoe.private@corp-mail.io" not in out


CONVOMEM = FIX / "convomem"


def test_convomem_writes_both_files_redacted(tmp_path: Path, fake_teich: None) -> None:
    out = tmp_path / "out"
    assert run(str(CONVOMEM), "--to", "convomem-conversations", "--out", str(out)) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "conversations.json",
        "conversations.message_ids.json",
    ]
    payload = json.loads((out / "conversations.json").read_text())
    convs = payload["evidence_items"][0]["conversations"]
    assert [c["id"] for c in convs] == ["sess-early", "sess-late"]
    assert convs[0]["messages"][0]["text"] == "EARLIER QUESTION"
    ids = json.loads((out / "conversations.message_ids.json").read_text())
    assert ids["sess-early"] == ["e1", "e2"]


def test_convomem_existing_output_refused(
    tmp_path: Path, fake_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "conversations.message_ids.json").write_text("keep")
    args = (str(CONVOMEM), "--to", "convomem-conversations", "--out", str(tmp_path))
    assert run(*args) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert (tmp_path / "conversations.message_ids.json").read_text() == "keep"
    assert not (tmp_path / "conversations.json").exists()


def test_convomem_out_in_input_dir_refused(
    tmp_path: Path, fake_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "in"
    shutil.copytree(CONVOMEM, src)
    assert run(str(src), "--to", "convomem-conversations", "--out", str(src)) == 1
    assert "input directory" in capsys.readouterr().err
    assert not (src / "conversations.json").exists()


def test_convomem_missing_teich_fails_cleanly(
    tmp_path: Path, no_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert run(str(CONVOMEM), "--to", "convomem-conversations", "--out", str(out)) == 1
    err = capsys.readouterr().err
    assert "pensieve-traces[redact]" in err and "--no-redact" in err
    assert list(out.iterdir()) == []


def test_convomem_no_redact_warns(
    tmp_path: Path, no_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    args = (str(CONVOMEM), "--to", "convomem-conversations", "--out", str(tmp_path), "--no-redact")
    assert run(*args) == 0
    assert "Redaction disabled" in capsys.readouterr().err
    assert "earlier question" in (tmp_path / "conversations.json").read_text()


def test_convomem_rejects_sidechains_flag(
    tmp_path: Path, fake_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    args = (str(CONVOMEM), "--to", "convomem-conversations", "--out", str(tmp_path / "o"))
    assert run(*args, "--include-sidechains") == 1
    assert "--include-sidechains" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


EXPORT = FIX / "claude_ai" / "export"
EXPORT_IDS = ["conv-plain", "conv-tools", "conv-branch", "conv-attach", "conv-empty"]


def _copy_export(tmp_path: Path) -> Path:
    dst = tmp_path / "export"
    shutil.copytree(EXPORT, dst)
    return dst


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.mark.parametrize("target", ["dir", "file"])
def test_convert_claude_ai_sts(tmp_path: Path, fake_teich: None, target: str) -> None:
    src = EXPORT if target == "dir" else EXPORT / "conversations.json"
    out = tmp_path / "out"
    assert run(str(src), "--to", "sts", "--out", str(out)) == 0
    assert sorted(p.stem for p in out.iterdir()) == sorted(EXPORT_IDS)
    header = json.loads((out / "conv-plain.jsonl").read_text().splitlines()[0])
    assert header["harness"] == "claude.ai"
    assert header["pensieveRedacted"] is True


def test_convert_claude_ai_convomem(tmp_path: Path, fake_teich: None) -> None:
    out = tmp_path / "out"
    assert run(str(EXPORT), "--to", "convomem-conversations", "--out", str(out)) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "conversations.json",
        "conversations.message_ids.json",
    ]
    payload = json.loads((out / "conversations.json").read_text())
    ids = {c["id"] for c in payload["evidence_items"][0]["conversations"]}
    assert ids <= set(EXPORT_IDS) and ids


@pytest.mark.parametrize("to", ["sts", "convomem-conversations"])
@pytest.mark.parametrize("out", [".", "sub"])
def test_claude_ai_out_in_export_dir_refused(
    tmp_path: Path, to: str, out: str, fake_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    export = _copy_export(tmp_path)
    before = _snapshot(export)
    assert run(str(export), "--to", to, "--out", str(export / out)) == 1
    assert _snapshot(export) == before
    assert not (export / "sub").exists()
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "not overwritten" in err


def test_claude_ai_out_beside_conversations_file_refused(
    tmp_path: Path, fake_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    export = _copy_export(tmp_path)
    before = _snapshot(export)
    args = (str(export / "conversations.json"), "--to", "convomem-conversations")
    assert run(*args, "--out", str(export)) == 1
    assert _snapshot(export) == before
    assert "not overwritten" in capsys.readouterr().err


def test_claude_ai_missing_teich_writes_nothing(
    tmp_path: Path, no_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert run(str(EXPORT), "--to", "sts", "--out", str(out)) == 1
    assert "redact" in capsys.readouterr().err
    assert not out.exists() or list(out.iterdir()) == []


def test_claude_ai_no_redact_warns(
    tmp_path: Path, no_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert run(str(EXPORT), "--to", "sts", "--out", str(out), "--no-redact") == 0
    assert len(list(out.iterdir())) == len(EXPORT_IDS)
    assert "warning" in capsys.readouterr().err


def test_ambiguous_dir_errors_and_from_resolves(
    tmp_path: Path, fake_teich: None, capsys: pytest.CaptureFixture[str]
) -> None:
    mixed = _copy_export(tmp_path)
    shutil.copy(SESSION, mixed / "sess-sts.jsonl")
    out = tmp_path / "out"
    assert run(str(mixed), "--to", "sts", "--out", str(out)) == 1
    err = capsys.readouterr().err
    assert "ambiguous" in err and "--from" in err and not out.exists()
    assert run(str(mixed), "--from", "claude-ai", "--to", "sts", "--out", str(out)) == 0
    assert sorted(p.stem for p in out.iterdir()) == sorted(EXPORT_IDS)
    out2 = tmp_path / "out2"
    assert run(str(mixed), "--from", "claude-code", "--to", "sts", "--out", str(out2)) == 0
    assert [p.name for p in out2.iterdir()] == ["sess-sts.jsonl"]


def test_dir_with_neither_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "empty").mkdir()
    assert run(str(tmp_path / "empty"), "--to", "sts", "--out", str(tmp_path / "o")) == 1
    assert "no *.jsonl session files or conversations.json" in capsys.readouterr().err


def test_from_claude_ai_without_export_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = (str(PROJECT), "--from", "claude-ai", "--to", "sts", "--out", str(tmp_path / "o"))
    assert run(*args) == 1
    assert "no conversations.json in" in capsys.readouterr().err


def test_malformed_export_is_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "export"
    bad.mkdir()
    (bad / "conversations.json").write_text("{not json")
    assert run(str(bad), "--to", "sts", "--out", str(tmp_path / "o")) == 1
    err = capsys.readouterr().err
    assert err.startswith("pensieve-traces: error:") and "Traceback" not in err
    assert err.count("\n") == 1


def test_load_sessions_mixed_inputs() -> None:
    cc = cli.load_sessions(PROJECT)
    ai = cli.load_sessions(EXPORT)
    assert {s.harness for s in cc} != {s.harness for s in ai}
    assert [s.id for s in ai] == EXPORT_IDS
    assert len(cli.load_sessions(SESSION)) == 1
    assert [
        s.id for s in cli.load_sessions(EXPORT / "conversations.json", "claude-ai")
    ] == EXPORT_IDS
