"""Factory lanes: create and maintain the lane hierarchy on GitHub with native features.

A lane is a parent issue labelled `lane`; its **native sub-issues**, in order, are the lane's issues.
Dependencies are native **"blocked by"** links. Priority is exactly one `P0`–`P3` label. These commands
wrap the GitHub REST calls (which need internal issue ids, not numbers) so the orchestrator doesn't
improvise them. Stdlib only; uses `gh`.

    python .claude/skills/factory/scripts/lanes.py sub-add 40 4 6        # lane #40: #4 then #6
    python .claude/skills/factory/scripts/lanes.py order 40 6 4          # reorder: #6 first
    python .claude/skills/factory/scripts/lanes.py block 8 --by 5        # #8 blocked by #5
    python .claude/skills/factory/scripts/lanes.py priority 4 P0         # exactly one P label
    python .claude/skills/factory/scripts/lanes.py project-add 40 4 6 --project 3   # + Priority
    python .claude/skills/factory/scripts/lanes.py show 40               # the lane's order
    python .claude/skills/factory/scripts/lanes.py --self-test
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys

PRIORITIES = ("P0", "P1", "P2", "P3")


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def repo() -> str:
    return _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()


def issue_id(r: str, number: int) -> int:
    """The REST id (not the number, not the node id) that the sub-issue and dependency APIs need."""
    return int(_gh("api", f"repos/{r}/issues/{number}", "-q", ".id").strip())


def sub_issues(r: str, lane: int) -> list[int]:
    return [i["number"] for i in json.loads(_gh("api", f"repos/{r}/issues/{lane}/sub_issues"))]


def sub_add(r: str, lane: int, numbers: list[int]) -> None:
    present = set(sub_issues(r, lane))
    for n in numbers:
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
        print(f"added #{n} to lane #{lane}")
    order(r, lane, numbers)


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
    for prev, n in itertools.pairwise(wanted):
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


def _self_test() -> None:
    assert PRIORITIES == ("P0", "P1", "P2", "P3")
    print("lanes.py self-test: ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("sub-add", help="add issues to a lane, in this order")
    p.add_argument("lane", type=int)
    p.add_argument("issues", type=int, nargs="+")
    p = sub.add_parser("order", help="reorder a lane's issues (these first, in this order)")
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
    args = parser.parse_args(argv)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
