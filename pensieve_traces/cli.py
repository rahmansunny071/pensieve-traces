"""Command line interface: ``pensieve-traces convert INPUT --to sts --out DIR``."""

from __future__ import annotations

import argparse
import re
import sys
import warnings
from collections.abc import Callable
from pathlib import Path

from pensieve_traces import upload as hub_upload
from pensieve_traces.model import Session
from pensieve_traces.readers import claude_ai
from pensieve_traces.readers.claude_ai import ClaudeAiParseError
from pensieve_traces.readers.claude_code import ClaudeCodeParseError, read_project, read_session
from pensieve_traces.redact import RedactionUnavailableError
from pensieve_traces.writers import convomem, sts

# Every writer has this signature; add one entry per ``--to`` target.
Writer = Callable[..., None]
WRITERS: dict[str, Writer] = {"sts": sts.write_session}
# Aggregate targets write one set of files for all sessions rather than one file per session.
CONVOMEM = "convomem-conversations"
TARGETS = sorted([*WRITERS, CONVOMEM])

_SAFE_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


class CliError(Exception):
    pass


SOURCES = ("claude-code", "claude-ai")
_EXPORT_FILE = "conversations.json"


def load_sessions(input_path: Path, source: str | None = None) -> list[Session]:
    """Read one input (any supported source) into sessions; the single input-detection point.

    ``source`` is ``None`` (detect), ``"claude-code"`` or ``"claude-ai"``. Detection:

    - a file named ``conversations.json`` is a claude.ai export; any other file is one Claude
      Code session ``.jsonl``;
    - a directory holding ``conversations.json`` and no ``*.jsonl`` is a claude.ai export;
      one holding ``*.jsonl`` and no ``conversations.json`` is a Claude Code project.

    Raises :class:`CliError` if the path is missing, a directory is ambiguous (holds both) or
    holds neither, or ``source`` forces a layout the path lacks; reader parse errors
    (:class:`ClaudeCodeParseError`, :class:`ClaudeAiParseError`) propagate. Prints nothing and
    does no redaction.
    """
    if source not in (None, *SOURCES):
        raise CliError(f"unknown source {source!r}; expected one of {', '.join(SOURCES)}")
    if input_path.is_dir():
        has_export = (input_path / _EXPORT_FILE).is_file()
        has_jsonl = any(p.is_file() for p in input_path.glob("*.jsonl"))
        if source is None:
            if has_export and has_jsonl:
                raise CliError(
                    f"ambiguous input {input_path}: it holds {_EXPORT_FILE} and *.jsonl files; "
                    "pass --from claude-code or --from claude-ai"
                )
            if not (has_export or has_jsonl):
                raise CliError(f"no *.jsonl session files or {_EXPORT_FILE} in {input_path}")
            source = "claude-ai" if has_export else "claude-code"
        if source == "claude-ai":
            if not has_export:
                raise CliError(f"no {_EXPORT_FILE} in {input_path}")
            return claude_ai.read_export(input_path)
        if not has_jsonl:
            raise CliError(f"no *.jsonl session files in {input_path}")
        return read_project(input_path)
    if input_path.is_file():
        if source is None:
            source = "claude-ai" if input_path.name == _EXPORT_FILE else "claude-code"
        if source == "claude-ai":
            return claude_ai.read_export(input_path)
        return [read_session(input_path)]
    raise CliError(f"no such file or directory: {input_path}")


def _check_out_dir(input_path: Path, out_dir: Path) -> None:
    """Refuse an output directory that could overwrite the input session files.

    Claude Code names session files ``<session id>.jsonl``, the same name ``convert`` writes,
    so writing into the input directory would silently replace the originals.
    """
    src = input_path.resolve()
    out = out_dir.resolve()
    if src.is_dir() and (out == src or out.is_relative_to(src)):
        raise CliError(
            f"--out {out_dir} is the input directory or inside it; "
            "choose another directory so the original session files are not overwritten"
        )
    if src.is_file() and out == src.parent:
        raise CliError(
            f"--out {out_dir} is the directory of the input file; "
            "choose another directory so the original session files are not overwritten"
        )


def _convert_convomem(args: argparse.Namespace, input_path: Path, sessions: list[Session]) -> None:
    if args.include_sidechains:
        raise CliError("--include-sidechains is not supported for convomem-conversations")
    out_dir = Path(args.out)
    _check_out_dir(input_path, out_dir)
    targets = [out_dir / convomem.CONVERSATIONS_FILE, out_dir / convomem.MESSAGE_IDS_FILE]
    existing = [str(p) for p in targets if p.exists()]
    if existing:
        raise CliError(
            f"refusing to overwrite {', '.join(existing)}; "
            "remove them or choose another --out directory"
        )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            paths = _write_convomem(sessions, out_dir, redact=not args.no_redact)
        except RedactionUnavailableError as exc:
            raise CliError(f"{exc}; or pass --no-redact to write unredacted output") from exc
        except FileExistsError as exc:
            raise CliError(str(exc)) from exc
    for w in caught:
        print(f"pensieve-traces: warning: {w.message}", file=sys.stderr)
    for path in paths:
        print(path)


def _write_convomem(sessions: list[Session], out_dir: Path, *, redact: bool) -> tuple[Path, Path]:
    # Like STS, the (empty) directory is created first; write_conversations redacts and builds
    # everything before it opens a file, so a redaction failure leaves no output files.
    out_dir.mkdir(parents=True, exist_ok=True)
    return convomem.write_conversations(sessions, out_dir, redact=redact)


def _convert(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    sessions = load_sessions(input_path, args.source)
    if args.to == CONVOMEM:
        _convert_convomem(args, input_path, sessions)
        return
    for session in sessions:
        if not _SAFE_ID.fullmatch(session.id):
            raise CliError(f"session id {session.id!r} cannot be used as a file name")
    out_dir = Path(args.out)
    _check_out_dir(input_path, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    writer = WRITERS[args.to]
    for session in sessions:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                writer(
                    session,
                    out_dir / f"{session.id}.jsonl",
                    redact=not args.no_redact,
                    include_sidechains=args.include_sidechains,
                )
            except RedactionUnavailableError as exc:
                raise CliError(f"{exc}; or pass --no-redact to write unredacted output") from exc
        for w in caught:
            print(f"pensieve-traces: warning: {w.message}", file=sys.stderr)
        print(out_dir / f"{session.id}.jsonl")


def _warn(message: str) -> None:
    print(f"pensieve-traces: warning: {message}", file=sys.stderr)


def _confirm() -> bool:
    if not sys.stdin.isatty():
        print("pensieve-traces: stdin is not a terminal; pass --yes to upload", file=sys.stderr)
        return False
    try:
        return input("Proceed with upload? [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _upload(args: argparse.Namespace) -> None:
    folder = Path(args.input)
    if not folder.is_dir():
        raise CliError(f"no such directory: {folder}")
    try:
        hub_upload.require_hub()
    except hub_upload.HubUnavailableError as exc:
        raise CliError(str(exc)) from exc
    files = hub_upload.collect_files(folder)
    if not files:
        raise CliError(f"no *.jsonl files in {folder}")
    unredacted = hub_upload.check_redaction(files)
    if unredacted:
        names = ", ".join(f.name for f in unredacted)
        if not args.allow_unredacted:
            raise CliError(
                f"refusing to upload files not marked as redacted: {names}; "
                "re-run `convert` with redaction on, or pass --allow-unredacted"
            )
        _warn(f"uploading files not marked as redacted: {names}")
    if args.public:
        _warn("--public: the dataset will be visible to everyone on the Hub")
    total = sum(f.stat().st_size for f in files)
    print(f"files:      {len(files)}")
    print(f"total size: {total} bytes")
    print(f"repo:       {args.repo}")
    print(f"visibility: {'PUBLIC' if args.public else 'private'}")
    chats = hub_upload.count_harness(files, claude_ai.HARNESS)
    if chats:
        _warn(
            f"{chats} {'file is' if chats == 1 else 'files are'} claude.ai chats "
            f'(harness "{claude_ai.HARNESS}"); redaction does not catch names, health details, '
            "money amounts or card and bank account numbers, and catches only some "
            "addresses and phone numbers. Review them before uploading."
        )
    if not args.yes and not _confirm():
        raise CliError("aborted; nothing uploaded")
    try:
        hub_upload.upload_files(files, args.repo, private=not args.public, notify=_warn)
    except hub_upload.HubUnavailableError as exc:
        raise CliError(str(exc)) from exc
    except hub_upload.UploadError as exc:
        raise CliError(str(exc)) from exc
    print(f"uploaded {len(files)} file(s) to {args.repo}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pensieve-traces")
    sub = parser.add_subparsers(dest="command", required=True)
    conv = sub.add_parser("convert", help="convert session traces to a shareable format")
    conv.add_argument(
        "input",
        help="a Claude Code session .jsonl file or project directory, "
        "or a claude.ai export directory or conversations.json",
    )
    conv.add_argument(
        "--from",
        dest="source",
        choices=SOURCES,
        default=None,
        help="input source (default: detected from the input)",
    )
    conv.add_argument("--to", required=True, choices=TARGETS, help="output format")
    conv.add_argument(
        "--out",
        required=True,
        help="output directory (sts: one file per session; convomem-conversations: two files)",
    )
    conv.add_argument(
        "--no-redact",
        action="store_true",
        help="write unredacted output (redaction is the default)",
    )
    conv.add_argument(
        "--include-sidechains", action="store_true", help="include subagent (sidechain) messages"
    )
    conv.set_defaults(func=_convert)
    up = sub.add_parser("upload", help="upload converted traces to a Hugging Face dataset")
    up.add_argument("input", help="directory of converted *.jsonl files")
    up.add_argument("--repo", required=True, help="dataset repo id, <user>/<name>")
    up.add_argument(
        "--public", action="store_true", help="make the dataset public (default: private)"
    )
    up.add_argument(
        "--allow-unredacted",
        action="store_true",
        help="upload files not marked as redacted",
    )
    up.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    up.set_defaults(func=_upload)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        args.func(args)
    except (CliError, ClaudeCodeParseError, ClaudeAiParseError, OSError) as exc:
        print(f"pensieve-traces: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
