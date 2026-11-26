"""Factory run log: one JSON line per orchestrator step, so a fresh session can resume an issue
mid-flight and the maintainer can see what each issue cost.

The log is a local cache and cost ledger. GitHub (issues, PRs, review threads, the PR's Handoff) is the
source of truth: entries POINT at it (pr, commit, thread ids) and never copy its content.

Logs live in `.factory/runs/<YYYY-MM-DD>.jsonl` at the repo root (gitignored). Stdlib only.

    python3 .claude/skills/factory/scripts/runlog.py add --issue 4 --step plan --model sonnet \\
        --tokens 41000 --ms 52000 --note "plan pushed"
    python3 .claude/skills/factory/scripts/runlog.py status  # last step per issue; in-flight ones
    python3 .claude/skills/factory/scripts/runlog.py summary  # wall time, rounds, tokens by model
    python3 .claude/skills/factory/scripts/runlog.py --self-test

Steps used by the factory skill: plan, plan-check, design-review, build, review, fix, handoff-written,
needs-maintainer, resume, merge, ready-to-merge, blocked, decision, improvement-filed. An issue whose
last step is `merge` is done; `needs-maintainer` and `ready-to-merge` are waiting on the maintainer;
anything else is in flight.

    python3 .claude/skills/factory/scripts/runlog.py add --issue 4 --step needs-maintainer \
        --actor reviewer --model opus --pr 29 --commit a1b2c3d --thread 7126001 --thread 7126002
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DONE_STEPS = {"merge"}
WAITING_STEPS = {"needs-maintainer", "ready-to-merge"}
# UTC that works on Python 3.10 and doesn't trip ruff's UP017 on 3.11+.
_UTC = timezone(timedelta(0))


def runs_dir() -> Path:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        top = "."
    return Path(top) / ".factory" / "runs"


def add(directory: Path, entry: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(_UTC)
    entry = {"ts": now.isoformat(timespec="seconds"), **entry}
    path = directory / f"{now.date().isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return path


def read_all(directory: Path) -> list[dict]:
    entries = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    return sorted(entries, key=lambda e: e["ts"])


def status(entries: list[dict]) -> dict[int, dict]:
    """Last entry per issue."""
    last: dict[int, dict] = {}
    for entry in entries:
        last[int(entry["issue"])] = entry
    return last


def summary(entries: list[dict]) -> dict[int, dict]:
    per: dict[int, dict] = defaultdict(
        lambda: {
            "first": None,
            "last": None,
            "fix_rounds": 0,
            "tokens": defaultdict(int),
            "by_actor": defaultdict(int),
            "steps": 0,
        }
    )
    for entry in entries:
        s = per[int(entry["issue"])]
        ts = datetime.fromisoformat(entry["ts"])
        s["first"] = s["first"] or ts
        s["last"] = ts
        s["steps"] += 1
        if entry.get("step") == "fix":
            s["fix_rounds"] += 1
        if entry.get("tokens"):
            s["tokens"][entry.get("model") or "unknown"] += int(entry["tokens"])
            s["by_actor"][entry.get("actor") or "unknown"] += int(entry["tokens"])
    return dict(per)


def _fmt_summary(per: dict[int, dict]) -> str:
    if not per:
        return "No run log entries."
    lines = [f"{'issue':>6}  {'wall':>8}  {'fixes':>5}  tokens by model | by role"]
    totals: dict[str, int] = defaultdict(int)
    for issue, s in sorted(per.items()):
        minutes = (s["last"] - s["first"]).total_seconds() / 60
        toks = ", ".join(f"{m}={t:,}" for m, t in sorted(s["tokens"].items())) or "-"
        roles = ", ".join(f"{a}={t:,}" for a, t in sorted(s["by_actor"].items()))
        if roles:
            toks += f" | {roles}"
        for m, t in s["tokens"].items():
            totals[m] += t
        lines.append(f"{'#' + str(issue):>6}  {minutes:>6.0f}m  {s['fix_rounds']:>5}  {toks}")
    grand = sum(totals.values())
    lines.append("")
    lines.append(
        "Total tokens: "
        + (", ".join(f"{m}={t:,}" for m, t in sorted(totals.items())) or "-")
        + f" (all models: {grand:,})"
    )
    return "\n".join(lines)


def _self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        add(d, {"issue": 4, "step": "plan", "model": "sonnet", "actor": "builder", "tokens": 40000})
        add(
            d, {"issue": 4, "step": "review", "model": "opus", "actor": "reviewer", "tokens": 60000}
        )
        add(d, {"issue": 4, "step": "fix", "model": "sonnet", "actor": "builder", "tokens": 9000})
        add(d, {"issue": 4, "step": "merge"})
        add(d, {"issue": 5, "step": "build", "model": "sonnet", "tokens": 30000})
        add(d, {"issue": 6, "step": "needs-maintainer", "pr": 9, "refs": {"threads": [1, 2]}})
        add(d, {"issue": 7, "step": "ready-to-merge", "pr": 70})
        entries = read_all(d)
        last = status(entries)
        assert last[7]["step"] in WAITING_STEPS and last[7]["step"] not in DONE_STEPS, last[7]
        assert last[5]["step"] not in WAITING_STEPS, last[5]
        assert last[4]["step"] == "merge" and last[5]["step"] == "build", last
        assert last[6]["refs"]["threads"] == [1, 2], last[6]
        per = summary(entries)
        assert per[4]["fix_rounds"] == 1, per[4]
        assert per[4]["tokens"]["sonnet"] == 49000 and per[4]["tokens"]["opus"] == 60000, per[4]
        assert per[4]["by_actor"]["builder"] == 49000, per[4]
        assert "#4" in _fmt_summary(per)
    print("runlog.py self-test: ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true", help="run offline checks and exit")
    parser.add_argument("--dir", type=Path, default=None, help="log directory (default: repo's)")
    sub = parser.add_subparsers(dest="cmd")
    a = sub.add_parser("add", help="append one step")
    a.add_argument("--issue", type=int, required=True)
    a.add_argument("--step", required=True)
    a.add_argument("--model", choices=["opus", "sonnet", "haiku"])
    a.add_argument("--actor", choices=["orchestrator", "builder", "reviewer", "writer"])
    a.add_argument("--tokens", type=int)
    a.add_argument("--ms", type=int, help="subagent duration in milliseconds")
    a.add_argument("--verdict")
    a.add_argument("--pr", type=int)
    a.add_argument("--commit", help="commit sha the step refers to")
    a.add_argument("--thread", action="append", type=int, help="review comment id (repeatable)")
    a.add_argument("--note", help="a short pointer, never copied content")
    sub.add_parser("status", help="last step per issue")
    sub.add_parser("summary", help="wall time, fix rounds and tokens per issue")
    args = parser.parse_args(argv)

    if args.self_test:
        _self_test()
        return 0
    directory = args.dir or runs_dir()
    if args.cmd == "add":
        entry = {k: v for k, v in vars(args).items() if k not in {"cmd", "dir", "self_test"}}
        refs = {"commit": entry.pop("commit", None), "threads": entry.pop("thread", None)}
        entry = {k: v for k, v in entry.items() if v is not None}
        refs = {k: v for k, v in refs.items() if v}
        if refs:
            entry["refs"] = refs
        print(add(directory, entry))
    elif args.cmd == "status":
        last = status(read_all(directory)) if directory.exists() else {}
        if not last:
            print("No run log entries.")
        for issue, entry in sorted(last.items()):
            step = entry.get("step")
            state = (
                "done"
                if step in DONE_STEPS
                else "waiting on maintainer"
                if step in WAITING_STEPS
                else "IN FLIGHT"
            )
            print(f"#{issue}: {state} (last step: {entry.get('step')} at {entry['ts']})")
    elif args.cmd == "summary":
        print(_fmt_summary(summary(read_all(directory)) if directory.exists() else {}))
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
