"""Command line interface.

- ``pensieve-traces convert INPUT --to sts|convomem-conversations --out DIR``
- ``pensieve-traces build convomem INPUT... --out DIR --model M``: a ConvoMem dataset from
  Claude Code sessions and claude.ai exports; redacted text goes to a model endpoint
  (local by default).
- ``pensieve-traces upload DIR --repo USER/NAME``: the only command that contacts the Hub.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import warnings
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from pensieve_traces import upload as hub_upload
from pensieve_traces.builders.convomem import (
    build_items,
    extract_candidates,
    item_prompt_hashes,
    prompt_hashes,
)
from pensieve_traces.builders.convomem import dataset as convomem_dataset
from pensieve_traces.builders.convomem.extract import ExtractStats
from pensieve_traces.builders.convomem.items import Stats
from pensieve_traces.builders.convomem.topics import Topic, TopicsError, load_topics, match_topics
from pensieve_traces.builders.llm import DEFAULT_BASE_URL, ChatClient, OpenAICompatibleClient
from pensieve_traces.model import Session
from pensieve_traces.readers import claude_ai
from pensieve_traces.readers.claude_ai import ClaudeAiParseError
from pensieve_traces.readers.claude_code import ClaudeCodeParseError, read_project, read_session
from pensieve_traces.redact import (
    RedactionUnavailableError,
    _default_anonymizer,
    prepare_for_output,
)
from pensieve_traces.writers import convomem, sts
from pensieve_traces.writers.convomem import sessions_to_conversations_with_ids

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


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def make_client(model: str, base_url: str, api_key_env: str | None) -> ChatClient:
    """Build the model client for ``build convomem``. Tests replace this function."""
    return OpenAICompatibleClient(model, base_url, api_key_env)


def _check_endpoint(base_url: str, allow_remote: bool) -> tuple[str, bool]:
    """Validate ``--base-url``; return ``(host[:port] for display, is_local)``."""
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError:
        raise CliError("--base-url is not a valid URL") from None
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise CliError("--base-url must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise CliError("--base-url must not contain credentials; use --api-key-env for a key")
    local = host in _LOCAL_HOSTS
    if not local and not allow_remote:
        raise CliError(
            f"--base-url host {host!r} is not localhost, 127.0.0.1 or [::1]; text from your "
            "sessions would leave this machine. Pass --allow-remote-model to send it there anyway"
        )
    shown = f"[{host}]" if ":" in host else host
    return (f"{shown}:{port}" if port is not None else shown), local


def _check_dataset_paths(inputs: list[Path], out_dir: Path, cache_dir: Path) -> None:
    """Refuse an ``--out`` or cache dir that could overwrite inputs or ship the cache."""
    out, cache = out_dir.resolve(), cache_dir.resolve()
    if out.exists() and not out.is_dir():
        raise CliError(f"--out {out_dir} exists and is not a directory")
    if not out.name:
        raise CliError("--out must not be the filesystem root")
    for path, label in ((out, "--out"), (cache, "the cache directory")):
        for src in (p.resolve() for p in inputs):
            if (src.is_dir() and (path == src or path.is_relative_to(src))) or (
                src.is_file() and path == src.parent
            ):
                raise CliError(
                    f"{label} {path} is an input or inside one; choose another directory so "
                    "the original session files are not overwritten"
                )
    if cache == out or cache.is_relative_to(out) or out.is_relative_to(cache):
        raise CliError("the cache directory and --out must not contain each other")
    if (out / "core_benchmark").exists() or (out / convomem_dataset.MANIFEST_FILE).exists():
        raise CliError(
            f"{out_dir} already holds core_benchmark/ or {convomem_dataset.MANIFEST_FILE}; "
            "remove them or choose another --out"
        )


def _build_convomem(args: argparse.Namespace) -> None:
    # 1. Endpoint and key, before any input is read.
    endpoint, endpoint_local = _check_endpoint(args.base_url, args.allow_remote_model)
    if args.api_key_env and not (os.environ.get(args.api_key_env) or "").strip():
        raise CliError(f"environment variable {args.api_key_env} is not set")
    redact = not args.no_redact
    # 2. Topics, so a bad file fails before any model call.
    topics: list[Topic] | None = None
    if args.topics:
        try:
            topics = load_topics(args.topics)
        except TopicsError as exc:
            raise CliError(f"--topics {args.topics}: {exc}") from exc
    # 3. Paths.
    inputs = [Path(i) for i in args.input]
    out_dir = Path(args.out).resolve()
    cache_dir = (
        Path(args.cache_dir).resolve()
        if args.cache_dir
        else out_dir.with_name(out_dir.name + ".cache")
    )
    _check_dataset_paths(inputs, out_dir, cache_dir)
    # 4. Read every input; one session per id (first input wins).
    originals: dict[str, Session] = {}
    read = 0
    for path in inputs:
        for session in load_sessions(path, args.source):
            read += 1
            originals.setdefault(session.id, session)
    sessions = list(originals.values())
    if not sessions:
        raise CliError("no sessions found in the inputs")
    # 5. Redact once over all inputs, in load order, with one anonymizer.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            anonymizer = _default_anonymizer() if redact else None
            prepared = [
                prepare_for_output(s, redact=redact, anonymizer=anonymizer) for s in sessions
            ]
        except RedactionUnavailableError as exc:
            raise CliError(f"{exc}; or pass --no-redact to send unredacted text") from exc
    for message in dict.fromkeys(str(w.message) for w in caught):
        _warn(message)
    topics_by_conv = {s.id: match_topics(topics, s) for s in sessions} if topics else None
    conversations, _ = sessions_to_conversations_with_ids(prepared)
    # 6. Client; the endpoint line comes before the first model call.
    what = "redacted text" if redact else "UNREDACTED text (redaction is off)"
    print(f"pensieve-traces: sending {what} to the model endpoint {endpoint}", file=sys.stderr)
    client = make_client(args.model, args.base_url, args.api_key_env)
    # 7. Model work; the only thing written so far is the cache.
    extract_stats, item_stats = ExtractStats(), Stats()
    candidates = extract_candidates(prepared, client, cache_dir=cache_dir, stats=extract_stats)
    if extract_stats.windows == 0:
        raise CliError("no conversation has any text to extract facts from")
    if extract_stats.failed_windows + extract_stats.dropped_bad_json == extract_stats.windows:
        raise CliError(
            "the model returned nothing usable for any conversation "
            f"({extract_stats.failed_windows} failed calls, {extract_stats.dropped_bad_json} "
            "unparseable replies); check the endpoint and --model"
        )
    items, item_stats = build_items(
        candidates, prepared, client, cache_dir=cache_dir, stats=item_stats
    )
    if not items:
        raise CliError(
            f"no evidence items were produced from {len(candidates)} candidate facts; "
            "nothing written (the cache is kept, so a rerun reuses the model replies)"
        )
    used = convomem_dataset.evidence_conversation_ids(items)
    manifest = convomem_dataset.build_manifest(
        inputs=len(inputs),
        sessions_read=read,
        duplicates_dropped=read - len(sessions),
        sessions_by_harness=dict(Counter(s.harness or "unknown" for s in sessions)),
        model=args.model,
        endpoint_local=endpoint_local,
        redaction=redact,
        prompt_hashes={"extract": prompt_hashes(), "items": item_prompt_hashes()},
        conversations={
            "total": len(conversations),
            "evidence": len(used),
            "filler": len(conversations) - len(used),
        },
        items_by_category=dict(item_stats.items_by_category),
        extract_stats=extract_stats.to_dict(),
        item_stats=item_stats.to_dict(),
        topics_enabled=topics is not None,
        topics_count=len(topics or []),
    )
    # 8. Write to a sibling temp dir, then move into place without replacing anything.
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{out_dir.name}.tmp-", dir=out_dir.parent))
    keep = False
    try:
        convomem_dataset.write_dataset(
            tmp,
            items,
            conversations,
            manifest,
            topics_by_conversation=topics_by_conv,
            topic_order=[t.name for t in topics or []],
        )
        convomem_dataset.commit_dataset(tmp, out_dir)
    except convomem_dataset.CommitError as exc:
        keep = exc.kept is not None
        raise CliError(str(exc)) from exc
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    by_category = ", ".join(f"{k}={v}" for k, v in item_stats.items_by_category.items() if v)
    print(f"pensieve-traces: {len(items)} items ({by_category})", file=sys.stderr)
    print(out_dir)


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
    build = sub.add_parser("build", help="build a dataset from session traces")
    build_sub = build.add_subparsers(dest="target", required=True)
    cm = build_sub.add_parser(
        "convomem",
        help="build a ConvoMem dataset (questions, answers, evidence) with a model",
    )
    cm.add_argument(
        "input",
        nargs="+",
        help="Claude Code session files or project directories, claude.ai exports, in any mix",
    )
    cm.add_argument("--out", required=True, help="dataset directory (core_benchmark/ inside)")
    cm.add_argument("--model", required=True, help="chat model name at the endpoint")
    cm.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"OpenAI-compatible endpoint (default {DEFAULT_BASE_URL})",
    )
    cm.add_argument("--api-key-env", help="name of the environment variable holding the API key")
    cm.add_argument(
        "--allow-remote-model",
        action="store_true",
        help="allow a --base-url that is not localhost (your text is sent there)",
    )
    cm.add_argument("--topics", help="JSON file mapping topic names to id/project/title matchers")
    cm.add_argument(
        "--cache-dir", help="model reply cache (default: <out>.cache, beside the dataset)"
    )
    cm.add_argument(
        "--from",
        dest="source",
        choices=SOURCES,
        default=None,
        help="input source for every input (default: detected per input)",
    )
    cm.add_argument(
        "--no-redact",
        action="store_true",
        help="send and write unredacted text (redaction is the default)",
    )
    cm.set_defaults(func=_build_convomem)
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
