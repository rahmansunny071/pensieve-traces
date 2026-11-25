"""Upload converted STS traces to a Hugging Face dataset repo (``pensieve-traces[hub]``).

Hub docs: https://huggingface.co/docs/hub/main/en/agent-traces

Authentication is whatever the user's own ``hf auth login`` stored; no token is read, passed or
printed here. Only the files whose redaction marker (header key ``pensieveRedacted``, written by
the STS writer) was checked are uploaded, each at the repo root.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pensieve_traces._version import install_command

__all__ = [
    "HubUnavailableError",
    "UploadError",
    "check_redaction",
    "collect_files",
    "count_harness",
    "require_hub",
    "upload_files",
]


class HubUnavailableError(ImportError):
    pass


class UploadError(Exception):
    pass


def collect_files(folder: str | Path) -> list[Path]:
    """Top-level ``*.jsonl`` files of ``folder`` (subdirectories are never included)."""
    return sorted(p for p in Path(folder).glob("*.jsonl") if p.is_file())


def _read_header(path: Path) -> dict[str, Any] | None:
    """The first line of ``path`` as a JSON object, or ``None`` if it is anything else."""
    try:
        with open(path, encoding="utf-8") as fh:
            first = fh.readline()
        header = json.loads(first)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return header if isinstance(header, dict) else None


def _is_marked_redacted(path: Path) -> bool:
    header = _read_header(path)
    return header is not None and header.get("pensieveRedacted") is True


def count_harness(files: list[Path], harness: str) -> int:
    """Number of files whose STS header line has exactly ``"harness": harness``.

    Reads only the first line of each file; anything odd counts as not matching.
    """
    count = 0
    for f in files:
        header = _read_header(f)
        if header is not None and header.get("harness") == harness:
            count += 1
    return count


def check_redaction(files: list[Path]) -> list[Path]:
    """Files NOT marked redacted. Reads only the first line; anything odd counts as unredacted."""
    return [f for f in files if not _is_marked_redacted(f)]


def _import_hub() -> Any:
    try:
        import huggingface_hub
    except ImportError as exc:
        raise HubUnavailableError(
            f"uploading needs huggingface_hub; install it with: {install_command('hub')}"
        ) from exc
    return huggingface_hub


def require_hub() -> None:
    """Raise :class:`HubUnavailableError` (with an install hint) if huggingface_hub is missing."""
    _import_hub()


def upload_files(
    files: list[Path],
    repo_id: str,
    *,
    private: bool = True,
    api: Any = None,
    notify: Callable[[str], None] | None = None,
) -> None:
    """Create the dataset repo (private unless ``private=False``) and commit exactly ``files``."""
    hub = _import_hub()
    api = api or hub.HfApi()
    say = notify or (lambda _msg: None)
    try:
        if api.repo_exists(repo_id, repo_type="dataset"):
            existing_private = api.dataset_info(repo_id).private is True
            if not existing_private and private:
                raise UploadError(
                    f"{repo_id} already exists and is public; refusing to upload private-by-default "
                    "traces to it (pass --public to upload anyway)"
                )
            if existing_private and not private:
                say(f"{repo_id} already exists and stays private; --public does not change it")
        api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
        api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=[
                hub.CommitOperationAdd(path_in_repo=f.name, path_or_fileobj=str(f)) for f in files
            ],
            commit_message="Upload traces via pensieve-traces",
        )
    except UploadError:
        raise
    except Exception as exc:  # hub errors vary by version; report type and message only
        raise UploadError(f"upload failed: {type(exc).__name__}: {exc}") from exc
