"""Optional ``--topics`` file: topic name to local matchers over conversations.

Format: ``{"home-care": {"ids": [...], "projects": [...], "titles": [...]}}``. Every key is an
optional list of non-empty strings and each topic needs at least one matcher. A conversation
matches a topic if its session id equals an ``ids`` entry, its ``project`` (the basename of the
session's working directory; claude.ai sessions have none) equals a ``projects`` entry ignoring
case, or its title contains a ``titles`` entry ignoring case. Matching is local: no model is
involved and the matcher text is never sent anywhere.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from pensieve_traces.model import Session

__all__ = ["Topic", "TopicsError", "load_topics", "match_topics"]

_KEYS = ("ids", "projects", "titles")


class TopicsError(ValueError):
    """The topics file is malformed. The message names the topic and key, never file content."""


@dataclass(frozen=True)
class Topic:
    name: str
    ids: frozenset[str]
    projects: frozenset[str]  # casefolded
    titles: tuple[str, ...]  # casefolded


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise TopicsError("duplicate key in the topics file")
    return dict(pairs)


def load_topics(path: str | os.PathLike[str]) -> list[Topic]:
    """Parse and validate ``path``; raises :class:`TopicsError` (``OSError`` if unreadable)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh, object_pairs_hook=_no_duplicates)
    except TopicsError:
        raise
    except ValueError:
        raise TopicsError("the topics file is not valid JSON") from None
    if not isinstance(data, dict):
        raise TopicsError("the topics file must be a JSON object of topic name to matchers")
    topics: list[Topic] = []
    for name, spec in data.items():
        if not name.strip():
            raise TopicsError("topic name must be non-empty")
        if not isinstance(spec, dict):
            raise TopicsError(f"topic {name!r}: matchers must be an object")
        for key in spec:
            if key not in _KEYS:
                raise TopicsError(f"topic {name!r}: unknown key {key!r}")
        lists: dict[str, list[str]] = {}
        for key in _KEYS:
            value = spec.get(key, [])
            if not isinstance(value, list) or not all(
                isinstance(v, str) and v.strip() for v in value
            ):
                raise TopicsError(f"topic {name!r}: {key!r} must be a list of non-empty strings")
            lists[key] = value
        if not any(lists.values()):
            raise TopicsError(f"topic {name!r}: no matchers")
        topics.append(
            Topic(
                name,
                frozenset(lists["ids"]),
                frozenset(v.casefold() for v in lists["projects"]),
                tuple(v.casefold() for v in lists["titles"]),
            )
        )
    return topics


def match_topics(topics: list[Topic], session: Session) -> list[str]:
    """Names of the topics ``session`` matches, in file order. Use the unredacted session."""
    title = (session.title or "").casefold()
    project = (session.project or "").casefold()
    return [
        t.name
        for t in topics
        if session.id in t.ids
        or (project and project in t.projects)
        or any(s in title for s in t.titles)
    ]
