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


def write(path: Path, marker: Any = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header: dict[str, Any] = {"type": "session", "harness": "h", "id": path.stem}
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
