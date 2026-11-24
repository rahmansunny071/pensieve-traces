"""Factory work queue: which open issues are ready to pick up, in dependency order.

An issue is READY when it is open, every issue on its "Depends on #A, #B" line is closed, and it
carries none of the in-progress or blocked labels. Within each state, issues labelled `improvement` come
after main-path issues: before a first release, the main path comes first. Reads issues with `gh`
(stdlib only).

    python .claude/skills/factory/scripts/queue.py            # table
    python .claude/skills/factory/scripts/queue.py --json     # machine-readable
    python .claude/skills/factory/scripts/queue.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field

IN_PROGRESS = ("factory:designing", "factory:building", "factory:review")
BLOCKED = "factory:blocked"
IMPROVEMENT = "improvement"
_DEPENDS_LINE = re.compile(r"^\W*depends on\b(?P<rest>.*)$", re.IGNORECASE | re.MULTILINE)
_ISSUE_REF = re.compile(r"(?<![\w/])#(\d+)\b")


@dataclass
class Item:
    number: int
    title: str
    state: str  # ready | waiting | in-progress | blocked
    depends_on: list[int] = field(default_factory=list)
    open_dependencies: list[int] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    improvement: bool = False


def dependencies(body: str) -> list[int]:
    """Issue numbers named on the body's "Depends on …" line(s), in order, without duplicates."""
    found: list[int] = []
    for match in _DEPENDS_LINE.finditer(body or ""):
        found += [int(n) for n in _ISSUE_REF.findall(match.group("rest"))]
    return list(dict.fromkeys(found))


def build_queue(issues: list[dict]) -> list[Item]:
    """Classify open issues; ready ones first, in dependency-then-number order."""
    closed = {i["number"] for i in issues if i["state"].upper() == "CLOSED"}
    items = []
    for issue in issues:
        if issue["state"].upper() != "OPEN":
            continue
        number = issue["number"]
        labels = [label["name"] for label in issue.get("labels", [])]
        deps = [d for d in dependencies(issue.get("body", "")) if d != number]
        waiting_on = [d for d in deps if d not in closed]
        if BLOCKED in labels:
            state = "blocked"
        elif any(label in labels for label in IN_PROGRESS):
            state = "in-progress"
        elif waiting_on:
            state = "waiting"
        else:
            state = "ready"
        items.append(
            Item(number, issue["title"], state, deps, waiting_on, labels, IMPROVEMENT in labels)
        )
    order = {"in-progress": 0, "ready": 1, "blocked": 2, "waiting": 3}
    return sorted(items, key=lambda item: (order[item.state], item.improvement, item.number))


def fetch_issues() -> list[dict]:
    out = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "all",
            "--limit",
            "500",
            "--json",
            "number,title,state,labels,body",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def render(items: list[Item]) -> str:
    if not items:
        return "No open issues."
    lines = [f"{'#':>5}  {'state':<11}  {'waits on':<12}  title"]
    for item in items:
        waits = ", ".join(f"#{d}" for d in item.open_dependencies) or "-"
        mark = " (improvement)" if item.improvement else ""
        lines.append(f"{item.number:>5}  {item.state:<11}  {waits:<12}  {item.title}{mark}")
    main = [f"#{i.number}" for i in items if i.state == "ready" and not i.improvement]
    extra = [f"#{i.number}" for i in items if i.state == "ready" and i.improvement]
    lines.append("")
    lines.append(f"Ready, main path first: {', '.join(main) if main else 'none'}")
    lines.append(f"Ready improvements: {', '.join(extra) if extra else 'none'}")
    return "\n".join(lines)


def _self_test() -> None:
    issues = [
        {"number": 1, "title": "model", "state": "CLOSED", "labels": [], "body": ""},
        {
            "number": 2,
            "title": "reader",
            "state": "OPEN",
            "labels": [],
            "body": "### Context\n\n- Depends on #1 (canonical model).\n- See docs/#7 and PR #99.",
        },
        {
            "number": 3,
            "title": "writer",
            "state": "OPEN",
            "labels": [],
            "body": "- Depends on #1 (model), #2 (reader) and #3 (itself).",
        },
        {
            "number": 4,
            "title": "redaction",
            "state": "OPEN",
            "labels": [{"name": "factory:blocked"}],
            "body": "Depends on #1",
        },
        {
            "number": 5,
            "title": "upload",
            "state": "OPEN",
            "labels": [{"name": "factory:review"}],
            "body": "",
        },
        {
            "number": 6,
            "title": "edge case",
            "state": "OPEN",
            "labels": [{"name": "improvement"}],
            "body": "- Depends on #1 (model).",
        },
        {"number": 7, "title": "writer", "state": "OPEN", "labels": [], "body": "Depends on #1"},
    ]
    assert dependencies(issues[1]["body"]) == [1]
    assert dependencies(issues[2]["body"]) == [1, 2, 3]
    queue = {item.number: item for item in build_queue(issues)}
    assert queue[2].state == "ready", queue[2]
    assert queue[3].state == "waiting" and queue[3].open_dependencies == [2], queue[3]
    assert queue[4].state == "blocked", queue[4]
    assert queue[5].state == "in-progress", queue[5]
    assert queue[6].state == "ready" and queue[6].improvement, queue[6]
    # ready main-path issues (2, 7) before the ready improvement (6)
    assert [i.number for i in build_queue(issues)] == [5, 2, 7, 6, 4, 3]
    print("queue.py self-test: ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--self-test", action="store_true", help="run offline checks and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    items = build_queue(fetch_issues())
    print(json.dumps([asdict(i) for i in items], indent=2) if args.json else render(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
