"""Upload tests. Offline: huggingface_hub is replaced by a fake module; no token, no network."""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from pensieve_traces import cli, upload


class FakeApi:
    existing: bool = False
    existing_private: bool | None = True
    calls: ClassVar[list[tuple[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        assert not args and not kwargs  # never a token

    def repo_exists(self, repo_id: str, **kw: Any) -> bool:
        FakeApi.calls.append(("repo_exists", (repo_id, kw)))
        return FakeApi.existing

    def dataset_info(self, repo_id: str, **kw: Any) -> Any:
        return types.SimpleNamespace(private=FakeApi.existing_private)

    def create_repo(self, repo_id: str, **kw: Any) -> None:
        assert "token" not in kw
        FakeApi.calls.append(("create_repo", (repo_id, kw)))

    def create_commit(self, **kw: Any) -> None:
        assert "token" not in kw
        FakeApi.calls.append(("create_commit", kw))


class FakeOp:
    def __init__(self, path_in_repo: str, path_or_fileobj: str) -> None:
        self.path_in_repo = path_in_repo
        self.path_or_fileobj = path_or_fileobj


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> type[FakeApi]:
    FakeApi.existing, FakeApi.existing_private, FakeApi.calls = False, True, []
    mod = types.ModuleType("huggingface_hub")
    mod.HfApi = FakeApi  # type: ignore[attr-defined]
    mod.CommitOperationAdd = FakeOp  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", mod)
    return FakeApi


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


def write(path: Path, marker: Any = True, harness: str = "h") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header: dict[str, Any] = {"type": "session", "harness": harness, "id": path.stem}
    if marker is not None:
        header["pensieveRedacted"] = marker
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")
    return path


def up(tmp: Path, *extra: str) -> int:
    return cli.main(["upload", str(tmp), "--repo", "me/ds", *extra])


def commit_call() -> dict[str, Any]:
    return next(v for k, v in FakeApi.calls if k == "create_commit")


def test_private_by_default_and_exact_files(tmp_path: Path, hub: type[FakeApi], capsys) -> None:
    write(tmp_path / "a.jsonl")
    write(tmp_path / "b.jsonl")
    write(tmp_path / "sub" / "c.jsonl")  # marked, but in a subdirectory: never uploaded
    (tmp_path / "notes.txt").write_text("x")
    assert up(tmp_path, "--yes") == 0
    create = next(v for k, v in FakeApi.calls if k == "create_repo")
    assert create == ("me/ds", {"repo_type": "dataset", "private": True, "exist_ok": True})
    call = commit_call()
    assert call["repo_type"] == "dataset" and call["repo_id"] == "me/ds"
    assert [(o.path_in_repo, o.path_or_fileobj) for o in call["operations"]] == [
        ("a.jsonl", str(tmp_path / "a.jsonl")),
        ("b.jsonl", str(tmp_path / "b.jsonl")),
    ]
    out = capsys.readouterr().out
    assert "files:      2" in out and "me/ds" in out and "private" in out


def test_public_warns(tmp_path: Path, hub: type[FakeApi], capsys) -> None:
    write(tmp_path / "a.jsonl")
    assert up(tmp_path, "--yes", "--public") == 0
    cap = capsys.readouterr()
    assert "PUBLIC" in cap.out and "visible to everyone" in cap.err
    assert next(v for k, v in FakeApi.calls if k == "create_repo")[1]["private"] is False


@pytest.mark.parametrize("marker", [None, False, "yes", 1])
def test_unmarked_refused(tmp_path: Path, hub: type[FakeApi], capsys, marker: Any) -> None:
    write(tmp_path / "a.jsonl", marker)
    assert up(tmp_path, "--yes") == 1
    assert "not marked as redacted: a.jsonl" in capsys.readouterr().err
    assert FakeApi.calls == []


def test_malformed_files_refused(tmp_path: Path) -> None:
    (tmp_path / "empty.jsonl").write_text("")
    (tmp_path / "bad.jsonl").write_text("not json\n")
    (tmp_path / "list.jsonl").write_text("[1]\n")
    (tmp_path / "bin.jsonl").write_bytes(b"\xff\xfe\n")
    files = upload.collect_files(tmp_path)
    assert upload.check_redaction(files) == files


def test_allow_unredacted_warns(tmp_path: Path, hub: type[FakeApi], capsys) -> None:
    write(tmp_path / "a.jsonl", None)
    assert up(tmp_path, "--yes", "--allow-unredacted") == 0
    assert "not marked as redacted" in capsys.readouterr().err
    assert len(commit_call()["operations"]) == 1


def test_prompt_no_aborts(
    tmp_path: Path, hub: type[FakeApi], tty: None, monkeypatch, capsys
) -> None:
    write(tmp_path / "a.jsonl")
    monkeypatch.setattr("builtins.input", lambda _p: "n")
    assert up(tmp_path) == 1
    assert FakeApi.calls == []
    assert "files:      1" in capsys.readouterr().out


def test_prompt_yes_uploads(tmp_path: Path, hub: type[FakeApi], tty: None, monkeypatch) -> None:
    write(tmp_path / "a.jsonl")
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    assert up(tmp_path) == 0
    assert any(k == "create_commit" for k, _ in FakeApi.calls)


def test_prompt_eof_aborts(tmp_path: Path, hub: type[FakeApi], tty: None, monkeypatch) -> None:
    write(tmp_path / "a.jsonl")

    def eof(_p: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert up(tmp_path) == 1
    assert FakeApi.calls == []


@pytest.mark.parametrize("private_value", [False, None])
def test_existing_public_repo_aborts(
    tmp_path: Path, hub: type[FakeApi], capsys, private_value
) -> None:
    write(tmp_path / "a.jsonl")
    FakeApi.existing, FakeApi.existing_private = True, private_value
    assert up(tmp_path, "--yes") == 1
    assert "already exists and is public" in capsys.readouterr().err
    assert not any(k == "create_commit" for k, _ in FakeApi.calls)


def test_public_flag_with_existing_private_repo_notes(
    tmp_path: Path, hub: type[FakeApi], capsys
) -> None:
    write(tmp_path / "a.jsonl")
    FakeApi.existing, FakeApi.existing_private = True, True
    assert up(tmp_path, "--yes", "--public") == 0
    assert "stays private" in capsys.readouterr().err


def test_no_jsonl_and_missing_dir(tmp_path: Path, hub: type[FakeApi]) -> None:
    assert up(tmp_path, "--yes") == 1
    assert cli.main(["upload", str(tmp_path / "nope"), "--repo", "me/ds"]) == 1


def test_missing_hub_extra(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    write(tmp_path / "a.jsonl")
    assert up(tmp_path, "--yes") == 1
    assert "pensieve-traces[hub]" in capsys.readouterr().err


def test_hub_error_reported_without_traceback(
    tmp_path: Path, hub: type[FakeApi], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    write(tmp_path / "a.jsonl")

    def boom(self: FakeApi, **kw: Any) -> None:
        raise RuntimeError("401")

    monkeypatch.setattr(FakeApi, "create_commit", boom)
    assert up(tmp_path, "--yes") == 1
    assert "upload failed: RuntimeError: 401" in capsys.readouterr().err


def test_convert_then_upload(tmp_path: Path, hub: type[FakeApi], monkeypatch) -> None:
    class Anon:
        def anonymize_text(self, text: str) -> str:
            return text

        def anonymize_value(self, value: Any) -> Any:
            return value

    mod = types.ModuleType("teich.anonymize")
    mod.TraceAnonymizer = Anon  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "teich", types.ModuleType("teich"))
    monkeypatch.setitem(sys.modules, "teich.anonymize", mod)
    src = Path(__file__).parent / "fixtures" / "sts" / "tool_session.jsonl"
    out = tmp_path / "out"
    assert cli.main(["convert", str(src), "--to", "sts", "--out", str(out)]) == 0
    assert up(out, "--yes") == 0
    assert [o.path_in_repo for o in commit_call()["operations"]] == ["sess-sts.jsonl"]
    # the same files converted with --no-redact are refused
    raw = tmp_path / "raw"
    assert cli.main(["convert", str(src), "--to", "sts", "--out", str(raw), "--no-redact"]) == 0
    assert up(raw, "--yes") == 1


def test_piped_stdin_without_yes_aborts(
    tmp_path: Path, hub: type[FakeApi], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    write(tmp_path / "a.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))  # StringIO.isatty() is False
    assert up(tmp_path) == 1
    assert not any(k in {"create_repo", "create_commit"} for k, _ in FakeApi.calls)
    assert "pass --yes" in capsys.readouterr().err


def test_missing_hub_checked_before_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    write(tmp_path / "a.jsonl")
    assert up(tmp_path) == 1  # no --yes: must fail before any prompt
    assert "files:" not in capsys.readouterr().out


# -- claude.ai chat warning --------------------------------------------------------------

CHAT_WARNING = "claude.ai chats"


def test_count_harness_exact_and_never_raises(tmp_path: Path) -> None:
    write(tmp_path / "ok.jsonl", harness="claude.ai")
    write(tmp_path / "case.jsonl", harness="Claude.ai")
    write(tmp_path / "code.jsonl", harness="claude_code")
    (tmp_path / "nokey.jsonl").write_text(json.dumps({"type": "session"}) + "\n")
    (tmp_path / "empty.jsonl").write_text("")
    (tmp_path / "bad.jsonl").write_text("not json\n")
    (tmp_path / "list.jsonl").write_text("[1]\n")
    (tmp_path / "bin.jsonl").write_bytes(b"\xff\xfe\n")
    assert upload.count_harness(upload.collect_files(tmp_path), "claude.ai") == 1


def test_warning_with_yes_names_count_and_gaps(tmp_path: Path, hub: type[FakeApi], capsys) -> None:
    write(tmp_path / "a.jsonl", harness="claude.ai")
    write(tmp_path / "b.jsonl", harness="claude.ai")
    write(tmp_path / "c.jsonl", harness="claude_code")
    assert up(tmp_path, "--yes") == 0
    err = capsys.readouterr().err
    assert (
        'pensieve-traces: warning: 2 files are claude.ai chats (harness "claude.ai"); '
        "redaction does not catch names, health details, money amounts or card and bank "
        "account numbers, and catches only some addresses and phone numbers. Review them before uploading."
    ) in err
    assert len(commit_call()["operations"]) == 3


def test_warning_singular(tmp_path: Path, hub: type[FakeApi], capsys) -> None:
    write(tmp_path / "a.jsonl", harness="claude.ai")
    assert up(tmp_path, "--yes") == 0
    assert "1 file is claude.ai chats" in capsys.readouterr().err
    assert len(commit_call()["operations"]) == 1


def test_no_warning_without_claude_ai_files(tmp_path: Path, hub: type[FakeApi], capsys) -> None:
    write(tmp_path / "a.jsonl", harness="claude_code")
    assert up(tmp_path, "--yes") == 0
    assert CHAT_WARNING not in capsys.readouterr().err
    assert len(commit_call()["operations"]) == 1


def test_warning_comes_before_prompt(
    tmp_path: Path, hub: type[FakeApi], tty: None, monkeypatch, capsys
) -> None:
    write(tmp_path / "a.jsonl", harness="claude.ai")
    seen: list[str] = []

    def fake_input(_prompt: str) -> str:
        seen.append(capsys.readouterr().err)
        return "y"

    monkeypatch.setattr("builtins.input", fake_input)
    assert up(tmp_path) == 0
    assert len(seen) == 1 and CHAT_WARNING in seen[0]
    assert len(commit_call()["operations"]) == 1


def test_warning_printed_on_non_tty_abort(tmp_path: Path, hub: type[FakeApi], capsys) -> None:
    write(tmp_path / "a.jsonl", harness="claude.ai")
    assert up(tmp_path) == 1
    err = capsys.readouterr().err
    assert CHAT_WARNING in err and "pass --yes" in err
    assert FakeApi.calls == []


def test_warning_with_allow_unredacted(tmp_path: Path, hub: type[FakeApi], capsys) -> None:
    write(tmp_path / "a.jsonl", None, harness="claude.ai")
    assert up(tmp_path, "--yes", "--allow-unredacted") == 0
    err = capsys.readouterr().err
    assert CHAT_WARNING in err and "not marked as redacted" in err
    assert len(commit_call()["operations"]) == 1
