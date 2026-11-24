# Design: `pensieve-traces upload` to a private HF dataset (#6)

## Problem
After `convert`, sharing means hand-running `hf upload`, with no check that the files were redacted and no guard against a public repo. Users need one command that uploads reviewed STS traces to a Hub dataset, private by default, and refuses unredacted files.

## Proposed approach
- Marker: STS allows extra header keys (not message keys). `session_to_sts_lines(session, *, include_sidechains=False, redacted: bool | None = None)` adds header key `pensieveRedacted` (bool) only when `redacted is not None`. `write_session` always passes `redacted=redact`, so the file records whether redaction ran. Goldens in `tests/fixtures/sts/` stay unchanged (the pure mapper defaults to no key); new writer tests assert the key via `write_session`.
- NEW `pensieve_traces/upload.py`: `collect_files(dir) -> list[Path]` (top-level `*.jsonl`); `check_redaction(files) -> list[Path]` returns files whose first line is not a JSON object with `pensieveRedacted is True` (missing, false, malformed, empty: all unredacted, fail closed); `upload_dataset(folder, repo_id, *, private=True, api=None) -> None`.
- huggingface_hub calls (checked against 1.8.0): import lazily; `api = huggingface_hub.HfApi()` (no token argument anywhere; auth is the user's `hf auth login`). `api.repo_exists(repo_id, repo_type="dataset")`; if it exists, `api.dataset_info(repo_id).private`; if the repo is public and `--public` was not given, abort (never put private-by-default traces in a public repo). Else `api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)`, then upload exactly the files whose marker was checked: `api.create_commit(repo_id=repo_id, repo_type="dataset", operations=[CommitOperationAdd(path_in_repo=f.name, path_or_fileobj=str(f)) for f in files], commit_message="Upload traces via pensieve-traces")`. `upload_folder(allow_patterns=...)` is not used: it matches with fnmatch, so `*` crosses `/` and `.jsonl` files in subfolders would go up unchecked. An existing repo with `private is not True` counts as public; with `--public` and an existing private repo, print that it stays private. `check_redaction` reads only the first line (decode error, empty file, non-dict line: unredacted). This reverses #4's "no extra header keys" decision.
- CLI `upload DIR --repo ID [--public] [--allow-unredacted] [--yes]`, registered next to `convert`. Flow: collect files (none: error); marker check (fail with the file list unless `--allow-unredacted`, which prints a warning); print summary (file count, total bytes, repo, visibility); `--public` prints a prominent warning; prompt `Proceed? [y/N]` via `input()` unless `--yes`; non-TTY without `--yes` aborts as "no". Then upload. Missing `huggingface_hub`: stderr hint `pip install 'pensieve-traces[hub]'`, exit 1, checked before any network. All errors exit 1; exceptions from the hub are reported as one stderr line without headers or tokens.
- pyproject: `[project.optional-dependencies] hub = ["huggingface_hub>=1.0,<3"]`; `dependencies` stays `[]`; `uv lock` regenerates `uv.lock`.

## Alternatives rejected
- Sidecar manifest as the marker: can drift from the files; the header travels with each file.
- Shelling out to `hf upload`: untestable offline and handles auth outside our control.

## Files & interfaces touched
- NEW `pensieve_traces/upload.py`
- CHANGED `pensieve_traces/writers/sts.py` (marker; docstring), `pensieve_traces/cli.py` (`upload` subcommand), `pyproject.toml`, `uv.lock`, `README.md`, `CHANGELOG.md`
- NEW `tests/test_upload.py`; CHANGED `tests/test_sts_writer.py`, `tests/test_cli.py`

## Test plan
Offline, runs under `--extra dev` alone. A fake `huggingface_hub` module in `sys.modules` (recording `HfApi` with `repo_exists`, `dataset_info`, `create_repo`, `create_commit`) is used; tests tmp-write synthetic STS files. Cases: private default passed to `create_repo`; `--public` warns; unmarked, false and malformed marker rejected, `--allow-unredacted` overrides with warning; summary contents; prompt yes/no/EOF and `--yes`; existing public repo aborts; no `.jsonl` errors; non-jsonl files and `.jsonl` files in subdirectories not uploaded (exact file list asserted); missing hub (simulated `sys.modules["huggingface_hub"]=None`) gives hint and exit 1; no token argument ever passed; `write_session` header carries `pensieveRedacted` true/false; existing goldens unchanged; `convert` output then `upload` end to end.

## Risks
- Privacy: marker is an attestation, not proof (a hand-edited file passes); redaction is best-effort. Default private; existing public repos blocked. Fixtures synthetic.
- Spec: extra key on the header only; Hub viewer behaviour with it unverified.
- Dependency: optional `hub` extra; base install and CI unaffected. `uv.lock` grows.

## Open questions
- Lower bound `>=1.0`: only 1.8.0 was inspected; the maintainer may prefer a wider range.
- Marker name `pensieveRedacted` (camelCase, matching STS keys): acceptable?
- Whether the viewer tolerates the extra header key was not verified.

## Size estimate
M

Range verified against huggingface_hub 1.33.0 and 2.4.0 (every API used is identical); `<3` keeps teich's transitive 2.4.0 in `uv.lock`.

## Implementation notes
- Built in PR #37: `pensieve_traces/upload.py` (`collect_files`, `check_redaction`, `require_hub`, `upload_files`) and an `upload DIR --repo ID [--public] [--allow-unredacted] [--yes]` subcommand in `cli.py`. New extra `hub = ["huggingface_hub>=1.0,<3"]`. The range was verified against 1.33.0 and 2.4.0, and `<3` keeps the huggingface-hub 2.4.0 that teich already pulls in.
- Redaction marker: `write_session` writes `pensieveRedacted` (bool) on the STS session header. This reverses #4's "no extra header keys" decision; the STS spec allows extra header metadata. `session_to_sts_lines(redacted=None)` omits the key, so the goldens are unchanged. The uploader fails closed: anything but a first-line JSON object with `pensieveRedacted: true` counts as unredacted.
- Design review (privacy): `upload_folder(allow_patterns="*.jsonl")` would have uploaded unchecked subdirectory files, because huggingface_hub's fnmatch `*` crosses `/`. The upload now uses `create_commit` + `CommitOperationAdd` for exactly the checked top-level files, and a test proves subdirectory files are excluded.
- Visibility: private by default. An existing repo with `private is not True` is refused unless `--public` is given. `--public` warns, and an existing private repo stays private.
- Code review fixes: the version bound (see above); a non-TTY stdin without `--yes` now aborts; the hub import is checked before the summary and prompt. No follow-up issues.
