"""Helpers shared by the ConvoMem builder steps: tolerant reply parsing and the reply cache.

``extract.py`` (candidate facts) and ``items.py`` (evidence items) both use them, so the parsing
rules and the cache format are defined once. Moved here unchanged from ``extract.py``; cache keys
and :data:`CACHE_FORMAT_VERSION` are the same as before.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

CACHE_FORMAT_VERSION = 1
_CACHE_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
_THINK = re.compile(r"\A\s*<think>.*?</think>", re.DOTALL)
_FENCE = re.compile(r"\A\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n?\s*```\s*\Z", re.DOTALL)


def strip_think(reply: str) -> str:
    """Drop one leading ``<think>...</think>`` block and surrounding whitespace."""
    return _THINK.sub("", reply, count=1).strip()


def parse_reply(reply: str) -> list[Any]:
    """Tolerant parse: drop a leading <think> block and one code fence, accept a list or a
    one-list-value object. Raises ``ValueError``/``TypeError`` for anything else."""
    text = strip_think(reply)
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    if isinstance(data, dict):
        if len(data) != 1 or not isinstance(next(iter(data.values())), list):
            raise TypeError("reply object is not a single list")
        data = next(iter(data.values()))
    if not isinstance(data, list):
        raise TypeError("reply is not a list")
    return data


def open_cache_dir(cache_dir: str | os.PathLike[str] | None) -> Path | None:
    """Create the cache directory if needed; ``NotADirectoryError`` if the path is a file."""
    if cache_dir is None:
        return None
    cache = Path(cache_dir)
    if cache.exists() and not cache.is_dir():
        raise NotADirectoryError(f"cache path is not a directory: {cache}")
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def cache_path(cache_dir: Path, messages: list[dict[str, str]], model: str) -> Path:
    blob = json.dumps(
        {"v": CACHE_FORMAT_VERSION, "model": model, "messages": messages}, sort_keys=True
    )
    return cache_dir / f"{hashlib.sha256(blob.encode()).hexdigest()}.json"


def cache_read(path: Path) -> list[Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("version") == CACHE_FORMAT_VERSION:
        reply = data.get("reply")
        if isinstance(reply, list):
            return reply
    return None


def cache_write(path: Path, reply: list[Any]) -> None:
    assert _CACHE_NAME.match(path.name)
    payload = json.dumps({"version": CACHE_FORMAT_VERSION, "reply": reply})
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
