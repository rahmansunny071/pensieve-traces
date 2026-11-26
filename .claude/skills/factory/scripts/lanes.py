"""Factory lanes: create and maintain the lane hierarchy on GitHub with native features.

A lane is a parent issue labelled `lane`; its **native sub-issues**, in order, are the lane's issues.
Dependencies are native **"blocked by"** links. Priority is exactly one `P0`–`P3` label. These commands
wrap the GitHub REST calls (which need internal issue ids, not numbers) so the orchestrator doesn't
improvise them. Stdlib only; uses `gh`.

    python3 .claude/skills/factory/scripts/lanes.py sub-add 40 4 6  # lane #40: append #4, #6 (last)
    python3 .claude/skills/factory/scripts/lanes.py order 40 6 4          # move #6 to the front
    python3 .claude/skills/factory/scripts/lanes.py block 8 --by 5        # #8 blocked by #5
    python3 .claude/skills/factory/scripts/lanes.py priority 4 P0         # exactly one P label
    python3 .claude/skills/factory/scripts/lanes.py project-add 40 4 6 --project 3   # + Priority
    python3 .claude/skills/factory/scripts/lanes.py show 40               # the lane's order
    python3 .claude/skills/factory/scripts/lanes.py --self-test

Documentation debt: one open issue titled exactly "Documentation tech-debt" (labels `documentation`,
`P2`) tracks doc todos as native sub-issues (labels `documentation`, `improvement`, `P2`).

    python3 .claude/skills/factory/scripts/lanes.py doc-debt find  # find or create; prints #N
    python3 .claude/skills/factory/scripts/lanes.py doc-debt add \\
        --title "README: …" --body-file f.md
    python3 .claude/skills/factory/scripts/lanes.py doc-debt add --issue 44  # link existing issue
    python3 .claude/skills/factory/scripts/lanes.py doc-debt list  # open todos, for ride-alongs
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

PRIORITIES = ("P0", "P1", "P2", "P3")
DOC_DEBT_TITLE = "Documentation tech-debt"  # same exact title in queue.py and bootstrap.sh
DOC_LABEL = "documentation"
DOC_DEBT_LABELS = (DOC_LABEL, "P2")
DOC_TODO_LABELS = (DOC_LABEL, "improvement", "P2")
DOC_DEBT_BODY = (  # the same text bootstrap.sh uses
    "Tracking issue for documentation todos (README, CHANGELOG, docstrings, design notes) that are"
    " not blockers.\n\n"
    "Each todo is a **sub-issue** of this one (labels `documentation`, `improvement`, `P2`). A fix can"
    " ride along with the next PR that touches the same file; that PR closes the sub-issue. Keep this"
    " title: the factory skill finds the issue by it.\n"
)
_ISSUE_URL = re.compile(r"/issues/(\d+)\s*$")


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def repo() -> str:
    return _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()


def issue_id(r: str, number: int) -> int:
    """The REST id (not the number, not the node id) that the sub-issue and dependency APIs need."""
    return int(_gh("api", f"repos/{r}/issues/{number}", "-q", ".id").strip())


def sub_issues(r: str, lane: int) -> list[int]:
    """The lane's sub-issue numbers in order (paginated: GitHub returns 30 per page)."""
    out = _gh("api", f"repos/{r}/issues/{lane}/sub_issues", "--paginate", "--jq", ".[].number")
    return [int(line) for line in out.splitlines() if line.strip()]


def sub_add(r: str, lane: int, numbers: list[int]) -> None:
    """Append issues to the end of a lane, in the order given.

    The lane's existing order is kept and issues already in it stay where they are. (`order` is the
    command that moves issues to the front; it is not used here to put new ones first.)
    """
    existing = sub_issues(r, lane)
    present = set(existing)
    added: list[int] = []
    for n in dict.fromkeys(numbers):
        if n in present:
            print(f"#{n} already in lane #{lane}")
            continue
        _gh(
            "api",
            "-X",
            "POST",
            f"repos/{r}/issues/{lane}/sub_issues",
            "-F",
            f"sub_issue_id={issue_id(r, n)}",
        )
        added.append(n)
        print(f"added #{n} to lane #{lane}")
    wanted = existing + added
    # GitHub appends a new sub-issue at the end, so this normally already holds
    if added and sub_issues(r, lane) != wanted:
        order(r, lane, wanted)


def order(r: str, lane: int, wanted: list[int]) -> None:
    current = sub_issues(r, lane)
    missing = [n for n in wanted if n not in current]
    if missing:
        raise SystemExit(f"not in lane #{lane}: {missing} (add them with sub-add first)")
    if current[: len(wanted)] == wanted:
        print(f"lane #{lane} order already {wanted}")
        return
    ids = {n: issue_id(r, n) for n in current}
    # place each wanted issue after the previous one; start by putting the first before the others
    first, front = wanted[0], current[0]
    if front != first:
        _gh(
            "api",
            "-X",
            "PATCH",
            f"repos/{r}/issues/{lane}/sub_issues/priority",
            "-F",
            f"sub_issue_id={ids[first]}",
            "-F",
            f"before_id={ids[front]}",
        )
    for prev, n in zip(wanted, wanted[1:]):  # noqa: RUF007 (itertools.pairwise is 3.10+)
        _gh(
            "api",
            "-X",
            "PATCH",
            f"repos/{r}/issues/{lane}/sub_issues/priority",
            "-F",
            f"sub_issue_id={ids[n]}",
            "-F",
            f"after_id={ids[prev]}",
        )
    print(f"lane #{lane} order now {sub_issues(r, lane)}")


def block(r: str, number: int, by: list[int]) -> None:
    existing = {
        i["number"]
        for i in json.loads(_gh("api", f"repos/{r}/issues/{number}/dependencies/blocked_by"))
    }
    for b in by:
        if b in existing:
            print(f"#{number} already blocked by #{b}")
            continue
        _gh(
            "api",
            "-X",
            "POST",
            f"repos/{r}/issues/{number}/dependencies/blocked_by",
            "-F",
            f"issue_id={issue_id(r, b)}",
        )
        print(f"#{number} is now blocked by #{b}")


def set_priority(r: str, number: int, level: str) -> None:
    labels = {
        lb["name"]
        for lb in json.loads(_gh("issue", "view", str(number), "-R", r, "--json", "labels"))[
            "labels"
        ]
    }
    args = ["issue", "edit", str(number), "-R", r, "--add-label", level]
    for other in PRIORITIES:
        if other != level and other in labels:
            args += ["--remove-label", other]
    _gh(*args)
    print(f"#{number} → {level}")


def project_add(r: str, numbers: list[int], project: int) -> None:
    owner = r.split("/", 1)[0]
    fields = json.loads(
        _gh("project", "field-list", str(project), "--owner", owner, "--format", "json")
    )
    prio = next((f for f in fields["fields"] if f["name"] == "Priority"), None)
    proj_id = json.loads(
        _gh("project", "view", str(project), "--owner", owner, "--format", "json")
    )["id"]
    for n in numbers:
        url = f"https://github.com/{r}/issues/{n}"
        item = json.loads(
            _gh(
                "project",
                "item-add",
                str(project),
                "--owner",
                owner,
                "--url",
                url,
                "--format",
                "json",
            )
        )
        level = next(
            (
                lb["name"]
                for lb in json.loads(_gh("issue", "view", str(n), "-R", r, "--json", "labels"))[
                    "labels"
                ]
                if lb["name"] in PRIORITIES
            ),
            None,
        )
        if prio and level:
            option = next((o for o in prio.get("options", []) if o["name"] == level), None)
            if option:
                _gh(
                    "project",
                    "item-edit",
                    "--id",
                    item["id"],
                    "--project-id",
                    proj_id,
                    "--field-id",
                    prio["id"],
                    "--single-select-option-id",
                    option["id"],
                )
        print(f"#{n} on project {project}" + (f" ({level})" if level else ""))


def pick_tracking(issues: list[dict]) -> int | None:
    """The tracking issue among `gh issue list --json number,title,state,labels` results: open, titled
    exactly DOC_DEBT_TITLE, labelled `documentation`. The oldest wins if there are several."""
    found = sorted(
        i["number"]
        for i in issues
        if i["title"] == DOC_DEBT_TITLE
        and i.get("state", "OPEN").upper() == "OPEN"
        and any(lb["name"] == DOC_LABEL for lb in i.get("labels", []))
    )
    if len(found) > 1:
        print(f"several {DOC_DEBT_TITLE!r} issues: {found}; using #{found[0]}", file=sys.stderr)
    return found[0] if found else None


def issue_number_from_url(url: str) -> int:
    m = _ISSUE_URL.search(url)
    if not m:
        raise SystemExit(f"unexpected output from gh issue create: {url!r}")
    return int(m.group(1))


def _label_names(r: str) -> set[str]:
    return {
        lb["name"]
        for lb in json.loads(_gh("label", "list", "-R", r, "--limit", "1000", "--json", "name"))
    }


def ensure_labels(r: str, names: tuple[str, ...]) -> None:
    have = _label_names(r)
    for name in names:
        if name not in have:
            _gh("label", "create", name, "-R", r)
            print(f"created label {name}", file=sys.stderr)


def _children(r: str, parent: int) -> list[dict]:
    """All sub-issues of `parent` (paginated: the tracking issue can outgrow one page)."""
    out = _gh(
        "api",
        f"repos/{r}/issues/{parent}/sub_issues",
        "--paginate",
        "--jq",
        ".[] | {number, title, state}",
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def doc_debt_find(r: str, create: bool = True) -> int | None:
    """The open "Documentation tech-debt" issue's number; create it (and its labels) if missing."""
    issues = []
    if DOC_LABEL in _label_names(r):
        fields = "number,title,state,labels"
        listing = ["issue", "list", "-R", r, "--state", "open", "--label", DOC_LABEL]
        issues = json.loads(_gh(*listing, "--limit", "1000", "--json", fields))
    n = pick_tracking(issues)
    if n is None and create:
        ensure_labels(r, DOC_DEBT_LABELS)
        args = ["issue", "create", "-R", r, "--title", DOC_DEBT_TITLE, "--body", DOC_DEBT_BODY]
        for label in DOC_DEBT_LABELS:
            args += ["--label", label]
        n = issue_number_from_url(_gh(*args).strip())
        print(f"created #{n}: {DOC_DEBT_TITLE}", file=sys.stderr)
    return n


def doc_debt_add(r: str, title: str | None, body_file: str | None, existing: int | None) -> int:
    """File a doc todo (or label an existing issue as one) and link it under the tracking issue."""
    parent = doc_debt_find(r)
    assert parent is not None
    ensure_labels(r, DOC_TODO_LABELS)
    if existing is None:
        args = ["issue", "create", "-R", r, "--title", title, "--body-file", body_file]
        for label in DOC_TODO_LABELS:
            args += ["--label", label]
        child = issue_number_from_url(_gh(*args).strip())
    else:
        child = existing
        args = ["issue", "edit", str(child), "-R", r]
        for label in DOC_TODO_LABELS:
            args += ["--add-label", label]
        _gh(*args)
    if any(c["number"] == child for c in _children(r, parent)):
        print(f"#{child} already under #{parent}", file=sys.stderr)
    else:
        _gh(
            "api",
            "-X",
            "POST",
            f"repos/{r}/issues/{parent}/sub_issues",
            "-F",
            f"sub_issue_id={issue_id(r, child)}",
        )
        print(f"#{child} is now a sub-issue of #{parent} ({DOC_DEBT_TITLE})", file=sys.stderr)
    return child


def doc_debt_list(r: str) -> list[dict]:
    """Open doc todos ({number, title}); empty when there's no tracking issue yet."""
    parent = doc_debt_find(r, create=False)
    if parent is None:
        return []
    todos = [c for c in _children(r, parent) if c["state"] == "open"]
    return [{"number": c["number"], "title": c["title"]} for c in todos]


def _self_test_sub_add() -> None:
    """sub-add appends; the fake `_gh` models GitHub: POST appends, PATCH priority moves."""
    global _gh
    lanes: dict = {}  # lane number to its ordered issue numbers
    prepend = False  # True models a GitHub whose POST puts the new issue first

    def fake(*args: str) -> str:
        path = next(a for a in args if a.startswith("repos/"))
        parts = path.split("/")  # repos, owner, name, issues, N, ...
        fields = dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("repos/"))
        if len(parts) == 5:  # issue id: id = number + 1000
            return f"{int(parts[4]) + 1000}\n"
        lane = lanes.setdefault(int(parts[4]), [])
        if "-X" not in args:  # GET --jq '.[].number': one number per line, 30 per page
            shown = lane if "--paginate" in args else lane[:30]  # as GitHub does
            return "".join(f"{n}\n" for n in shown)
        moved = int(fields["sub_issue_id"]) - 1000
        if parts[-1] == "sub_issues":  # POST: GitHub appends at the end
            lane.insert(0 if prepend else len(lane), moved)
        else:  # PATCH .../priority with before_id or after_id
            lane.remove(moved)
            if "before_id" in fields:
                lane.insert(lane.index(int(fields["before_id"]) - 1000), moved)
            else:
                lane.insert(lane.index(int(fields["after_id"]) - 1000) + 1, moved)
        return "{}"

    real, _gh = _gh, fake
    try:
        sub_add("o/r", 1, [5, 3, 9])  # a new lane keeps the given order
        assert lanes[1] == [5, 3, 9], lanes
        sub_add("o/r", 1, [4])  # appends, does not jump ahead
        assert lanes[1] == [5, 3, 9, 4], lanes
        sub_add("o/r", 1, [8, 3, 2])  # #3 is already there and stays put
        assert lanes[1] == [5, 3, 9, 4, 8, 2], lanes
        order("o/r", 1, [2])  # order is the way to move one to the front
        assert lanes[1] == [2, 5, 3, 9, 4, 8], lanes
        lanes[7] = list(range(100, 135))  # more than one page of 30
        assert sub_issues("o/r", 7) == list(range(100, 135))
        prepend = True  # POST prepends: sub_add's order(wanted) fallback restores the order
        sub_add("o/r", 8, [5, 3])
        assert lanes[8] == [5, 3], lanes
        sub_add("o/r", 8, [9, 4])
        assert lanes[8] == [5, 3, 9, 4], lanes
    finally:
        _gh = real


def _self_test() -> None:
    assert PRIORITIES == ("P0", "P1", "P2", "P3")

    def iss(n, title=DOC_DEBT_TITLE, state="OPEN", labels=(DOC_LABEL, "P2")):
        return {
            "number": n,
            "title": title,
            "state": state,
            "labels": [{"name": x} for x in labels],
        }

    assert pick_tracking([]) is None
    assert pick_tracking([iss(43)]) == 43
    assert pick_tracking([iss(50), iss(43)]) == 43  # the oldest wins
    assert pick_tracking([iss(43, state="CLOSED")]) is None
    assert pick_tracking([iss(43, labels=("P2",))]) is None  # must carry `documentation`
    assert pick_tracking([iss(43, title="Documentation tech-debt (old)")]) is None  # exact title
    assert pick_tracking([iss(43, title="documentation tech-debt")]) is None
    assert issue_number_from_url("https://github.com/o/r/issues/44\n") == 44
    assert DOC_TODO_LABELS == ("documentation", "improvement", "P2")
    _self_test_sub_add()
    print("lanes.py self-test: ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser(
        "sub-add",
        help="append issues to the end of a lane, in this order (keeps the existing order)",
    )
    p.add_argument("lane", type=int)
    p.add_argument("issues", type=int, nargs="+")
    p = sub.add_parser(
        "order", help="move these issues to the front of a lane, in this order (sub-add appends)"
    )
    p.add_argument("lane", type=int)
    p.add_argument("issues", type=int, nargs="+")
    p = sub.add_parser("block", help='add native "blocked by" links')
    p.add_argument("issue", type=int)
    p.add_argument("--by", type=int, nargs="+", required=True)
    p = sub.add_parser("priority", help="set exactly one P0–P3 label")
    p.add_argument("issue", type=int)
    p.add_argument("level", choices=PRIORITIES)
    p = sub.add_parser("project-add", help="add issues to the Project board (sets Priority)")
    p.add_argument("issues", type=int, nargs="+")
    p.add_argument("--project", type=int, required=True)
    p = sub.add_parser("show", help="print a lane's sub-issues in order")
    p.add_argument("lane", type=int)
    p = sub.add_parser("doc-debt", help=f"the {DOC_DEBT_TITLE!r} tracking issue and its todos")
    dd = p.add_subparsers(dest="doc_cmd", required=True)
    dd.add_parser("find", help="print the tracking issue's number, creating it if missing")
    q = dd.add_parser("add", help="file a doc todo (or link an existing issue) as a sub-issue")
    src = q.add_mutually_exclusive_group(required=True)
    src.add_argument("--title", help="title of a new todo (needs --body-file)")
    src.add_argument("--issue", type=int, help="an existing issue to link instead")
    q.add_argument("--body-file", help="body of the new todo: which doc, what's wrong, the fix")
    q = dd.add_parser("list", help="open todos (number and title), for the builder's ride-alongs")
    q.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "doc-debt" and args.doc_cmd == "add" and args.title and not args.body_file:
        parser.error("doc-debt add --title needs --body-file")
    if args.self_test:
        _self_test()
        return 0
    if not args.cmd:
        parser.print_help()
        return 0
    r = repo()
    if args.cmd == "sub-add":
        sub_add(r, args.lane, args.issues)
    elif args.cmd == "order":
        order(r, args.lane, args.issues)
    elif args.cmd == "block":
        block(r, args.issue, args.by)
    elif args.cmd == "priority":
        set_priority(r, args.issue, args.level)
    elif args.cmd == "project-add":
        project_add(r, args.issues, args.project)
    elif args.cmd == "show":
        print(" → ".join(f"#{n}" for n in sub_issues(r, args.lane)) or "(no sub-issues)")
    elif args.cmd == "doc-debt":
        if args.doc_cmd == "find":
            print(f"#{doc_debt_find(r)}")
        elif args.doc_cmd == "add":
            print(f"#{doc_debt_add(r, args.title, args.body_file, args.issue)}")
        else:
            todos = doc_debt_list(r)
            if args.json:
                print(json.dumps(todos, indent=2))
            else:
                print("\n".join(f"#{t['number']}  {t['title']}" for t in todos) or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
