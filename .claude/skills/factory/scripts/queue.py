"""Factory work queue: what to do next, worked out from GitHub (the source of truth).

States, in the order the orchestrator should handle them:
- in-progress: labelled factory:designing / factory:building / factory:review.
- needs-maintainer (replied): labelled factory:needs-maintainer, and the maintainer has answered on the
  PR since the factory last spoke. Resume these first.
- ready: open, every issue on its "Depends on #A, #B" line is closed, no factory label. Main-path
  issues come before issues labelled `improvement`.
- needs-maintainer (waiting): asked, not answered yet. Leave it, and anything that depends on it.
- blocked: labelled factory:blocked.
- waiting: open dependencies.

Reads issues and PRs with `gh` (stdlib only). The maintainer is the logged-in `gh` user unless
--maintainer is given.

    python .claude/skills/factory/scripts/queue.py            # table
    python .claude/skills/factory/scripts/queue.py --json     # machine-readable
    python .claude/skills/factory/scripts/queue.py --offline  # skip the reply check (issues only)
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
NEEDS_MAINTAINER = "factory:needs-maintainer"
BLOCKED = "factory:blocked"
IMPROVEMENT = "improvement"
_DEPENDS_LINE = re.compile(r"^\W*depends on\b(?P<rest>.*)$", re.IGNORECASE | re.MULTILINE)
_ISSUE_REF = re.compile(r"(?<![\w/])#(\d+)\b")
# (PR number, maintainer replied?). An alias, so no type hint ends in a double "]".
ReplyInfo = tuple[int, bool]
_CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b", re.IGNORECASE)


@dataclass
class Item:
    number: int
    title: str
    state: str  # in-progress | needs-maintainer | ready | blocked | waiting
    depends_on: list[int] = field(default_factory=list)
    open_dependencies: list[int] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    improvement: bool = False
    pr: int | None = None
    replied: bool | None = None  # needs-maintainer only; None when not checked


def dependencies(body: str) -> list[int]:
    """Issue numbers named on the body's "Depends on …" line(s), in order, without duplicates."""
    found: list[int] = []
    for match in _DEPENDS_LINE.finditer(body or ""):
        found += [int(n) for n in _ISSUE_REF.findall(match.group("rest"))]
    return list(dict.fromkeys(found))


def has_new_reply(threads: list[dict], comments: list[dict], maintainer: str) -> bool:
    """True when the maintainer has spoken last somewhere on the PR.

    `threads`: unresolved review threads, each {"comments": [{"author": login, "createdAt": iso}, …]}.
    `comments`: PR conversation comments, each {"author": login, "createdAt": iso}.
    A thread counts when its last comment is the maintainer's; conversation comments count when the
    maintainer's newest one is newer than everyone else's newest one.
    """
    for thread in threads:
        if thread.get("comments") and thread["comments"][-1].get("author") == maintainer:
            return True
    mine = [c["createdAt"] for c in comments if c.get("author") == maintainer]
    others = [c["createdAt"] for c in comments if c.get("author") != maintainer]
    return bool(mine) and (not others or max(mine) > max(others))


def build_queue(issues: list[dict], replies: dict[int, ReplyInfo] | None = None) -> list[Item]:
    """Classify open issues. `replies` maps issue number → (PR number, maintainer replied?)."""
    replies = replies or {}
    closed = {i["number"] for i in issues if i["state"].upper() == "CLOSED"}
    items = []
    for issue in issues:
        if issue["state"].upper() != "OPEN":
            continue
        number = issue["number"]
        labels = [label["name"] for label in issue.get("labels", [])]
        deps = [d for d in dependencies(issue.get("body", "")) if d != number]
        waiting_on = [d for d in deps if d not in closed]
        pr, replied = replies.get(number, (None, None))
        if BLOCKED in labels:
            state = "blocked"
        elif NEEDS_MAINTAINER in labels:
            state = "needs-maintainer"
        elif any(label in labels for label in IN_PROGRESS):
            state = "in-progress"
        elif waiting_on:
            state = "waiting"
        else:
            state = "ready"
        items.append(
            Item(
                number,
                issue["title"],
                state,
                deps,
                waiting_on,
                labels,
                IMPROVEMENT in labels,
                pr,
                replied if state == "needs-maintainer" else None,
            )
        )

    def rank(item: Item) -> int:
        if item.state == "needs-maintainer":
            return 1 if item.replied else 3
        return {"in-progress": 0, "ready": 2, "blocked": 4, "waiting": 5}[item.state]

    return sorted(items, key=lambda item: (rank(item), item.improvement, item.number))


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def fetch_issues() -> list[dict]:
    out = _gh(
        "issue",
        "list",
        "--state",
        "all",
        "--limit",
        "500",
        "--json",
        "number,title,state,labels,body",
    )
    return json.loads(out)


_THREADS_QUERY = """
query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){ pullRequest(number:$n){
  reviewThreads(first:100){ nodes{ isResolved
    comments(first:50){ nodes{ author{login} createdAt } } } }
  comments(first:100){ nodes{ author{login} createdAt } } } } }
"""


def fetch_replies(issue_numbers: list[int], maintainer: str) -> dict[int, ReplyInfo]:
    """For issues waiting on the maintainer: their open PR and whether the maintainer has replied."""
    if not issue_numbers:
        return {}
    repo = json.loads(_gh("repo", "view", "--json", "owner,name"))
    owner, name = repo["owner"]["login"], repo["name"]
    prs = json.loads(_gh("pr", "list", "--state", "open", "--json", "number,body,headRefName"))
    result: dict[int, ReplyInfo] = {}
    for issue in issue_numbers:
        pr = next(
            (
                p["number"]
                for p in prs
                if p["headRefName"] in (f"local/issue-{issue}", f"factory/issue-{issue}")
                or str(issue) in _CLOSES.findall(p.get("body") or "")
            ),
            None,
        )
        if pr is None:
            continue
        data = json.loads(
            _gh(
                "api",
                "graphql",
                "-f",
                f"query={_THREADS_QUERY}",
                "-f",
                f"o={owner}",
                "-f",
                f"r={name}",
                "-F",
                f"n={pr}",
            )
        )
        node = data["data"]["repository"]["pullRequest"]

        def flat(cs: list[dict]) -> list[dict]:
            return [
                {"author": (c.get("author") or {}).get("login"), "createdAt": c["createdAt"]}
                for c in cs
            ]

        threads = [
            {"comments": flat(t["comments"]["nodes"])}
            for t in node["reviewThreads"]["nodes"]
            if not t["isResolved"]
        ]
        result[issue] = (pr, has_new_reply(threads, flat(node["comments"]["nodes"]), maintainer))
    return result


def render(items: list[Item]) -> str:
    if not items:
        return "No open issues."
    lines = [f"{'#':>5}  {'state':<18}  {'waits on':<12}  title"]
    for item in items:
        waits = ", ".join(f"#{d}" for d in item.open_dependencies) or "-"
        state = item.state
        if state == "needs-maintainer":
            state = "needs-you:replied" if item.replied else "needs-you"
        mark = " (improvement)" if item.improvement else ""
        pr = f" [PR #{item.pr}]" if item.pr else ""
        lines.append(f"{item.number:>5}  {state:<18}  {waits:<12}  {item.title}{mark}{pr}")
    replied = [f"#{i.number}" for i in items if i.state == "needs-maintainer" and i.replied]
    asked = [f"#{i.number}" for i in items if i.state == "needs-maintainer" and not i.replied]
    main = [f"#{i.number}" for i in items if i.state == "ready" and not i.improvement]
    extra = [f"#{i.number}" for i in items if i.state == "ready" and i.improvement]
    lines.append("")
    lines.append(f"Resume first (maintainer replied): {', '.join(replied) if replied else 'none'}")
    lines.append(f"Ready, main path first: {', '.join(main) if main else 'none'}")
    lines.append(f"Ready improvements: {', '.join(extra) if extra else 'none'}")
    lines.append(f"Waiting on the maintainer: {', '.join(asked) if asked else 'none'}")
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
        {
            "number": 8,
            "title": "asked, answered",
            "state": "OPEN",
            "labels": [{"name": NEEDS_MAINTAINER}],
            "body": "",
        },
        {
            "number": 9,
            "title": "asked, not answered",
            "state": "OPEN",
            "labels": [{"name": NEEDS_MAINTAINER}],
            "body": "",
        },
    ]
    assert dependencies(issues[1]["body"]) == [1]
    assert dependencies(issues[2]["body"]) == [1, 2, 3]

    # reply detection
    bot, me = "claude[bot]", "maint"
    t_answered = {"comments": [{"author": bot, "createdAt": "1"}, {"author": me, "createdAt": "2"}]}
    t_open = {"comments": [{"author": bot, "createdAt": "1"}]}
    assert has_new_reply([t_open, t_answered], [], me)
    assert not has_new_reply([t_open], [], me)
    assert has_new_reply(
        [], [{"author": bot, "createdAt": "1"}, {"author": me, "createdAt": "2"}], me
    )
    assert not has_new_reply(
        [], [{"author": me, "createdAt": "1"}, {"author": bot, "createdAt": "2"}], me
    )

    queue = build_queue(issues, {8: (31, True), 9: (32, False)})
    by = {item.number: item for item in queue}
    assert by[2].state == "ready", by[2]
    assert by[3].state == "waiting" and by[3].open_dependencies == [2], by[3]
    assert by[4].state == "blocked", by[4]
    assert by[5].state == "in-progress", by[5]
    assert by[6].state == "ready" and by[6].improvement, by[6]
    assert by[8].state == "needs-maintainer" and by[8].replied and by[8].pr == 31, by[8]
    assert by[9].state == "needs-maintainer" and by[9].replied is False, by[9]
    # in-progress, replied, ready main path (2, 7), ready improvement (6), waiting on maintainer,
    # blocked, waiting on dependencies
    assert [i.number for i in queue] == [5, 8, 2, 7, 6, 9, 4, 3], [i.number for i in queue]
    assert "Resume first (maintainer replied): #8" in render(queue)
    print("queue.py self-test: ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--offline", action="store_true", help="don't check PRs for replies")
    parser.add_argument("--maintainer", help="GitHub login (default: the logged-in gh user)")
    parser.add_argument("--self-test", action="store_true", help="run offline checks and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    issues = fetch_issues()
    replies: dict[int, ReplyInfo] = {}
    if not args.offline:
        waiting = [
            i["number"]
            for i in issues
            if i["state"].upper() == "OPEN"
            and any(label["name"] == NEEDS_MAINTAINER for label in i.get("labels", []))
        ]
        if waiting:
            maintainer = args.maintainer or _gh("api", "user", "-q", ".login").strip()
            replies = fetch_replies(waiting, maintainer)
    items = build_queue(issues, replies)
    print(json.dumps([asdict(i) for i in items], indent=2) if args.json else render(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
