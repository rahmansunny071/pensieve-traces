"""Command line interface: ``pensieve-traces convert INPUT --to sts --out DIR``."""

from __future__ import annotations

import argparse
import re
import sys
import warnings
from collections.abc import Callable
from pathlib import Path

from pensieve_traces.model import Session
from pensieve_traces.readers.claude_code import ClaudeCodeParseError, read_project, read_session
from pensieve_traces.redact import RedactionUnavailableError
from pensieve_traces.writers import sts

# Every writer has this signature; add one entry per ``--to`` target.
Writer = Callable[..., None]
WRITERS: dict[str, Writer] = {"sts": sts.write_session}

_SAFE_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


class CliError(Exception):
    pass


def _load(input_path: Path) -> list[Session]:
    if input_path.is_dir():
        if not any(p.is_file() for p in input_path.glob("*.jsonl")):
            raise CliError(f"no *.jsonl session files in {input_path}")
        return read_project(input_path)
    if input_path.is_file():
        return [read_session(input_path)]
    raise CliError(f"no such file or directory: {input_path}")


def _convert(args: argparse.Namespace) -> None:
    sessions = _load(Path(args.input))
    for session in sessions:
        if not _SAFE_ID.fullmatch(session.id):
            raise CliError(f"session id {session.id!r} cannot be used as a file name")
    out_dir = Path(args.out)
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pensieve-traces")
    sub = parser.add_subparsers(dest="command", required=True)
    conv = sub.add_parser("convert", help="convert session traces to a shareable format")
    conv.add_argument("input", help="a session .jsonl file or a project directory")
    conv.add_argument("--to", required=True, choices=sorted(WRITERS), help="output format")
    conv.add_argument("--out", required=True, help="output directory (one file per session)")
    conv.add_argument(
        "--no-redact",
        action="store_true",
        help="write unredacted output (redaction is the default)",
    )
    conv.add_argument(
        "--include-sidechains", action="store_true", help="include subagent (sidechain) messages"
    )
    conv.set_defaults(func=_convert)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        args.func(args)
    except (CliError, ClaudeCodeParseError, OSError) as exc:
        print(f"pensieve-traces: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
