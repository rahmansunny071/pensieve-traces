"""Deterministic guards for the sync-root-factory skill.

Brings a downstream repo up to a release TAG of rahmansunny071/amplifactory. This script does
everything that can be checked by code (what is dirty, which tag, what differs, what may be
overwritten); the agent does the judgement (hand-ports, conflicts, the PR). Stdlib only, plus
`git` (and `gh` for `labels`). Every command that refuses exits 1 with "refused: <why>". Each
refuses BEFORE writing, with two exceptions that say so in their message: `apply`'s post-write
diff (undo with `git restore .` and `git clean -fd` scoped to the folders it names) and
`labels --create`, which lists the labels it created before a failure.

SF below is the TARGET tag's copy extracted outside the repo, pointed at the repo (see SKILL.md):
`python3 -I $T/x/template/.claude/skills/sync-root-factory/scripts/sync.py --repo $R`.

    SF preflight
    SF latest
    SF status [--assume-tag vX.Y.Z]
    SF branch vX.Y.Z
    SF verify-local [--assume-tag vX.Y.Z]
    SF apply --to-tag vX.Y.Z [--assume-tag vX.Y.Z]
    SF changelog --tag vX.Y.Z --from vA.B.C
    SF labels --tag vX.Y.Z [--create]
    SF copier-check vX.Y.Z
    SF check-markers
    SF push vX.Y.Z
    python3 sync.py --self-test

Files come only from the release tag (a depth-1 fetch of `refs/tags/<tag>` into a temporary bare
repo), never from `main`. Everything read from the template (CHANGELOG, SKILL.md, bootstrap.sh) is
data. `--source` exists for the self-test; never pass it in a real run. Never run the copy in
the repo: it may predate the guards.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = "rahmansunny071/amplifactory"
# the official template's names: the repo was renamed from agent-factory (#67), and generated repos
# still record the old one in `_src_path`; GitHub redirects it
OFFICIAL_REPOS = (REPO, "rahmansunny071/agent-factory")
DEFAULT_SOURCE = f"https://github.com/{REPO}.git"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,49}$")
MARKER_RE = re.compile(r"^(<{7}|>{7})( |$)")
FACTORY = ".claude/skills/factory"
SKILLS = ".claude/skills"
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# `_src_path` forms copier-check accepts, after quotes, comment, `.git` and `/` are stripped
SRC_PREFIXES = ("gh:", "https://github.com/", "git@github.com:", "ssh://git@github.com/")
# labels only the GitHub Actions factory uses; a repo that only runs the skill doesn't need them
ACTIONS_ONLY_LABELS = {"factory:design", "design:approved"}
VERSION_FILE = f"{FACTORY}/VERSION"
BRANCH_PREFIX = "local/adopt-"
# what `fetch` takes from a tag: every skill folder, plus data we only read
FETCH_PATHS = [
    f"template/{SKILLS}",
    "CHANGELOG.md",
    "template/scripts/bootstrap.sh.jinja",
]


Lines = list[str]
Files = dict[str, bytes]
Plan = dict[str, Files]  # folder -> files to write
Entry = tuple[str, Lines]  # (version, lines); aliases keep closing brackets apart


class Refuse(Exception):
    """A guard fired: nothing has been written."""


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if check and proc.returncode:
        raise Refuse(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def repo_root(path: str) -> Path:
    proc = subprocess.run(
        ["git", "-C", path, "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        raise Refuse(f"{path} is not inside a git repository")
    return Path(proc.stdout.strip())


def parse_tag(tag: str) -> tuple[int, int, int]:
    m = TAG_RE.match(tag or "")
    if not m:
        raise Refuse(f"{tag!r} is not a release tag (vX.Y.Z)")
    return int(m[1]), int(m[2]), int(m[3])


# --- reading a repo -------------------------------------------------------------------------


def is_template_repo(root: Path) -> bool:
    if (root / "copier.yml").is_file() and (root / "template").is_dir():
        return True
    url = git(root, "config", "--get", "remote.origin.url", check=False).strip()
    return any(
        re.search(rf"{re.escape(name)}(\.git)?/?$", url, re.IGNORECASE) for name in OFFICIAL_REPOS
    )


def dirty_entries(root: Path) -> list[str]:
    out = git(root, "status", "--porcelain", "--untracked-files=all")
    return [line for line in out.splitlines() if line]


def preflight(root: Path) -> dict:
    if is_template_repo(root):
        raise Refuse("this is amplifactory itself (or a fork of it); the sync never runs here")
    dirty = dirty_entries(root)
    if dirty:
        raise Refuse("working tree is not clean (commit or stash first):\n  " + "\n  ".join(dirty))
    kind = "generated" if (root / ".copier-answers.yml").is_file() else "shadowing"
    return {"root": str(root), "kind": kind}


def tree_reader(root: Path):
    def read(path: str) -> str | None:
        p = root / path
        return p.read_text(encoding="utf-8") if p.is_file() else None

    return read


def ref_reader(root: Path, ref: str):
    def read(path: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(root), "show", f"{ref}:{path}"],
            check=False,
            capture_output=True,
            text=True,
        )
        return proc.stdout if proc.returncode == 0 else None

    return read


def current_version(read, assume: str | None) -> tuple[str, str]:
    """(kind, current). Generated: `_commit`, as written. Shadowing: VERSION, or --assume-tag."""
    answers = read(".copier-answers.yml")
    if answers is not None:
        m = re.search(r"^_commit:\s*['\"]?([^\s'\"]+)", answers, re.MULTILINE)
        if not m:
            raise Refuse(".copier-answers.yml has no _commit")
        return "generated", m[1]
    text = read(VERSION_FILE)
    recorded = None
    if text is not None:
        v = text.strip()
        if not VERSION_RE.match(v):
            raise Refuse(f"{VERSION_FILE} holds {v!r}, expected X.Y.Z")
        recorded = f"v{v}"
    if assume is not None:
        parse_tag(assume)
        if recorded is not None and recorded != assume:
            raise Refuse(f"--assume-tag {assume} disagrees with {VERSION_FILE} ({recorded})")
        return "shadowing", assume
    if recorded is None:
        raise Refuse(
            f"no {VERSION_FILE}: pass --assume-tag <tag the skill folder was copied from>; "
            "verify-local still checks that claim against the tag before anything is written"
        )
    return "shadowing", recorded


def compare_versions(current: str, latest: str) -> str:
    m = TAG_RE.match(current)
    if not m:
        raise Refuse(
            f"current version {current!r} is not a release tag (a copier describe string?); "
            "report it as-is and ask the maintainer"
        )
    cur, lat = parse_tag(current), parse_tag(latest)
    if cur == lat:
        return "up-to-date"
    if cur > lat:
        raise Refuse(f"current {current} is newer than the latest tag {latest}: not downgrading")
    return "behind"


# --- the template's tags --------------------------------------------------------------------


def latest_tag(source: str) -> str:
    proc = subprocess.run(
        ["git", "ls-remote", "--tags", source], capture_output=True, text=True, check=False
    )
    if proc.returncode:
        raise Refuse(f"cannot list tags of {source}: {proc.stderr.strip()}")
    tags = []
    for line in proc.stdout.splitlines():
        ref = line.split("\t")[-1]
        if not ref.startswith("refs/tags/") or ref.endswith("^{}"):
            continue
        name = ref[len("refs/tags/") :]
        if TAG_RE.match(name):
            tags.append(name)
    if not tags:
        raise Refuse(f"{source} has no vX.Y.Z tags")
    return max(tags, key=parse_tag)


def _fetch_bare(bare: Path, tag: str, source: str) -> str:
    """Depth-1 fetch of refs/tags/<tag> into a fresh bare repo; refuse unless it is annotated."""
    parse_tag(tag)
    git(bare, "init", "--bare", "-q")
    ref = f"refs/tags/{tag}"
    git(bare, "fetch", "-q", "--depth", "1", source, f"{ref}:{ref}")
    if git(bare, "cat-file", "-t", ref).strip() != "tag":
        raise Refuse(f"{tag} is not an annotated tag")
    return ref


def check_annotated(tag: str, source: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _fetch_bare(Path(tmp), tag, source)


def fetch_tag(tag: str, dest: Path, source: str) -> list[str]:
    """Extract the tag's skill folders, CHANGELOG and bootstrap.sh into dest. Tag only."""
    parse_tag(tag)
    if dest.exists() and any(dest.iterdir()):
        raise Refuse(f"{dest} is not empty")
    with tempfile.TemporaryDirectory() as tmp:
        bare = Path(tmp)
        ref = _fetch_bare(bare, tag, source)
        present = [
            p for p in FETCH_PATHS if git(bare, "ls-tree", "--name-only", ref, "--", p).strip()
        ]
        if not present:  # `git archive <ref> --` with no paths would archive the whole tree
            raise Refuse(f"{tag} has none of the paths this script reads")
        proc = subprocess.run(
            ["git", "-C", str(bare), "archive", "--format=tar", ref, "--", *present],
            capture_output=True,
            check=False,
        )
        if proc.returncode:
            raise Refuse(f"git archive {tag} failed: {proc.stderr.decode(errors='replace')}")
        members = []
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            for m in tar.getmembers():
                parts = Path(m.name).parts
                if m.name.startswith("/") or ".." in parts:
                    raise Refuse(f"{tag}: unsafe path in archive: {m.name}")
                if m.isdir():
                    continue
                if not m.isfile():
                    raise Refuse(f"{tag}: archive member {m.name} is a link or special file")
                members.append((m, tar.extractfile(m).read()))  # type: ignore[union-attr]
        dest.mkdir(parents=True, exist_ok=True)
        for m, data in members:
            target = dest / m.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if m.mode & 0o111:
                target.chmod(0o755)
    return sorted(m.name for m, _ in members)


def tag_files(tree: Path, folder: str) -> dict[str, bytes]:
    base = tree / "template" / folder
    out = {}
    if base.is_dir():
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out[p.relative_to(tree / "template").as_posix()] = p.read_bytes()
    return out


# --- comparing and replacing the two skill folders -----------------------------------------


def _is_ignored(root: Path, rel: str) -> bool:
    return (
        subprocess.run(["git", "-C", str(root), "check-ignore", "-q", rel], check=False).returncode
        == 0
    )


def check_no_symlinks(root: Path, folder: str) -> None:
    """Refuse a folder (or a parent) that is a symlink or resolves outside the repo."""
    cur = root
    for part in Path(folder).parts:
        cur = cur / part
        if cur.is_symlink():
            raise Refuse(f"{cur.relative_to(root)} is a symlink; refusing to write through it")
    if not cur.resolve().is_relative_to(root.resolve()):
        raise Refuse(f"{folder} resolves outside the repository")
    if cur.is_dir():
        for p in cur.rglob("*"):
            if p.is_symlink():
                raise Refuse(f"{p.relative_to(root)} is a symlink inside {folder}")


def local_tracked(root: Path, folder: str) -> list[str]:
    out = git(root, "ls-files", "-z", "--", folder)
    return sorted(p for p in out.split("\0") if p)


def local_untracked(root: Path, folder: str) -> list[str]:
    out = git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", folder)
    return sorted(p for p in out.split("\0") if p)


def skill_dir(name: str) -> str:
    return f"{SKILLS}/{name}"


def folder_names(tree: Path, strict: bool) -> list[str]:
    """Skill folders a tag ships. Plain files directly under skills/ are ignored. strict: a name
    outside SKILL_NAME_RE refuses everything (fail closed)."""
    base = tree / "template" / SKILLS
    names = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    bad = [n for n in names if not SKILL_NAME_RE.match(n)]
    if strict and bad:
        raise Refuse(f"the tag ships skill folders with unsafe names: {bad}")
    return names


def exists_locally(root: Path, name: str) -> bool:
    """From the filesystem, not git: ignored folders, dangling symlinks and case-only variants
    (macOS is case-insensitive) all count as existing."""
    base = root / SKILLS
    if os.path.lexists(base / name):
        return True
    try:
        return any(e.lower() == name.lower() for e in os.listdir(base))
    except OSError:
        return False


def check_skills_dir(root: Path) -> None:
    for d in (root / ".claude", root / SKILLS):
        if os.path.lexists(d) and not d.is_symlink() and not d.is_dir():
            raise Refuse(f"{d.relative_to(root)} is a file, not a directory")


def differences(root: Path, tree: Path, names: list[str] | None = None) -> list[str]:
    """Local skill folders vs the tag's copy, for the named folders (default: all the tag has).
    A folder wholly absent locally is skipped (apply re-adds it); a partly deleted one differs.
    VERSION is ignored when the tag predates it."""
    check_skills_dir(root)
    diffs: list[str] = []
    for name in folder_names(tree, strict=False) if names is None else names:
        folder = skill_dir(name)
        want = tag_files(tree, folder)
        check_no_symlinks(root, folder)
        if not exists_locally(root, name):
            continue
        have = set(local_tracked(root, folder))
        if VERSION_FILE not in want:
            have.discard(VERSION_FILE)
        diffs += [f"untracked: {p}" for p in local_untracked(root, folder)]
        for rel, data in want.items():
            if rel not in have:
                diffs.append(f"missing locally: {rel}")
            elif (root / rel).read_bytes() != data:
                diffs.append(f"modified: {rel}")
        diffs += [f"only local: {rel}" for rel in sorted(have - set(want))]
    return diffs


def verify_local(root: Path, tree: Path, tag: str, names: list[str] | None = None) -> None:
    if names is None and not folder_names(tree, strict=False):
        raise Refuse(
            f"{tag} ships no skill folders, so there is nothing to compare with: the claim that "
            "this repo was copied from it cannot be verified"
        )
    diffs = differences(root, tree, names)
    if diffs:
        raise Refuse(
            f"the skill folders differ from {tag}; stop and list these for the maintainer "
            "(restore the tag's copy by hand if the edits are unwanted):\n  " + "\n  ".join(diffs)
        )


def current_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def plan_apply(root: Path, old: Path, new: Path, current: str, to_tag: str) -> tuple[Plan, Lines]:
    """Every check for every folder, before anything is written. Returns (folder -> files, notes)."""
    check_skills_dir(root)
    new_names = folder_names(new, strict=True)
    old_names = folder_names(old, strict=False)
    plan: Plan = {}
    notes: list[str] = []
    branch = BRANCH_PREFIX + to_tag
    for name in new_names:
        folder = skill_dir(name)
        check_no_symlinks(root, folder)
        present = exists_locally(root, name)
        if name not in old_names:
            if present:
                raise Refuse(
                    f"{folder} exists locally but {current} did not ship it, so it cannot be "
                    "verified and apply will not overwrite it. On "
                    f"{branch}: rename it if it is your own skill, or `git rm -r {folder}` if it "
                    "is a hand copy (this includes a `sync-root-factory/` committed by the v0.8.0 "
                    "bootstrap); commit; rerun apply"
                )
        elif not present:
            notes.append(f"re-adding (absent locally): {name}")
        else:
            verify_local(root, old, current, [name])  # old and new only
        plan[folder] = tag_files(new, folder)
    for name in old_names:
        if name not in new_names and exists_locally(root, name):
            notes.append(f"no longer shipped by {to_tag}, left alone: {name}")
    version = plan.get(FACTORY, {}).get(VERSION_FILE)
    if version is not None and version.decode().strip() != to_tag[1:]:
        raise Refuse(
            f"{to_tag}'s {VERSION_FILE} says {version.decode().strip()!r}: "
            "the release forgot to bump it; tell the maintainer"
        )
    tracked = set(local_tracked(root, SKILLS))
    for files in plan.values():  # every path to write stays inside the repo and is committable
        for rel in files:
            if not (root / rel).resolve().is_relative_to(root.resolve()):
                raise Refuse(f"{rel} would be written outside the repository")
            if rel not in tracked and os.path.lexists(root / rel):
                raise Refuse(f"{rel} exists locally but git does not track it; not writing over it")
            if rel not in tracked and _is_ignored(root, rel):
                raise Refuse(f"{rel} is gitignored: it would be written but never committed")
    return plan, notes


def apply_sync(
    root: Path, source: str, to_tag: str, assume: str | None, notes: list[str] | None = None
) -> list[str]:
    to = parse_tag(to_tag)
    dirty = dirty_entries(root)
    if dirty:
        raise Refuse("working tree is not clean:\n  " + "\n  ".join(dirty))
    if current_branch(root) != BRANCH_PREFIX + to_tag:
        raise Refuse(f"apply runs only on {BRANCH_PREFIX}{to_tag} (run `branch` first)")
    kind, current = current_version(tree_reader(root), assume)
    if kind != "shadowing":
        raise Refuse("generated repos use `copier update`, not apply")
    if parse_tag(current) >= to:
        raise Refuse(f"already on {current}; {to_tag} is not newer")
    with tempfile.TemporaryDirectory() as tmp:
        old, new = Path(tmp) / "old", Path(tmp) / "new"
        fetch_tag(current, old, source)
        fetch_tag(to_tag, new, source)
        plan, found = plan_apply(root, old, new, current, to_tag)
        if notes is not None:
            notes += found
        written = []
        for folder, files in plan.items():
            for rel in local_tracked(root, folder):
                if rel == VERSION_FILE and VERSION_FILE not in files:
                    continue
                (root / rel).unlink()
            for rel, data in files.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                if (new / "template" / rel).stat().st_mode & 0o111:
                    target.chmod(0o755)
                written.append(rel)
            for d in sorted((p for p in (root / folder).rglob("*") if p.is_dir()), reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()
        bad = post_diff(root, new, list(plan))
    if bad:
        raise Refuse(
            "diff -rq after apply failed. Files were written; undo with "
            f"`git restore .` and `git clean -fd -- {' '.join(plan)}`:\n  " + "\n  ".join(bad)
        )
    return sorted(written)


def post_diff(root: Path, tree: Path, folders: list[str]) -> list[str]:
    """Filesystem vs the tag's copy, ignoring gitignored entries (e.g. __pycache__)."""
    bad = []
    for folder in folders:
        want = tag_files(tree, folder)
        have = {}
        for p in (root / folder).rglob("*"):
            rel = p.relative_to(root).as_posix()
            if p.is_file() and not _is_ignored(root, rel):
                have[rel] = p.read_bytes()
        if VERSION_FILE not in want:
            have.pop(VERSION_FILE, None)
        bad += [f"differs: {r}" for r, data in want.items() if have.get(r) != data]
        bad += [f"extra: {r}" for r in have if r not in want]
    return bad


def answer_value(text: str | None, key: str) -> str | None:
    """A top-level `key: value` from .copier-answers.yml: quotes and a trailing comment dropped."""
    if text is None or len(re.findall(rf"^{re.escape(key)}:", text, re.MULTILINE)) != 1:
        return None  # absent, or repeated: Copier keeps the LAST one, so no first match is safe
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", text, re.MULTILINE)
    if not m:
        return None
    v = m[1].strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        return v[1:end] if end > 0 else None
    return re.split(r"\s+#", v, maxsplit=1)[0].strip()


def src_path_ok(read) -> bool:
    v = answer_value(read(".copier-answers.yml"), "_src_path")
    if not v:
        return False
    v = v.removesuffix("/").removesuffix(".git")  # once each, as in the URLs Copier builds
    # the scheme or alias is case-sensitive, as in Copier (`GH:` would be a local path); the
    # owner/repo part is not
    return any(
        v.startswith(prefix) and v[len(prefix) :].lower() == name.lower()
        for prefix in SRC_PREFIXES
        for name in OFFICIAL_REPOS
    )


def copier_check(root: Path, tag: str, source: str) -> None:
    """Before `copier update`: a release tag that is annotated, a `_commit` that exists upstream
    and a `_src_path` that is the GitHub template (not a local clone or a fork)."""
    parse_tag(tag)
    read = tree_reader(root)
    if read(".copier-answers.yml") is None:
        raise Refuse("no .copier-answers.yml: this is not a generated repo")
    text = read(".copier-answers.yml") or ""
    for key in ("_src_path", "_commit"):
        if len(re.findall(rf"^{key}:", text, re.MULTILINE)) > 1:
            raise Refuse(
                f"{key} appears more than once in .copier-answers.yml; Copier keeps the last one. "
                f"Edit the existing {key} line in place (replace it; never add a second line), "
                "commit, and rerun this check"
            )
    commit = answer_value(text, "_commit") or ""
    if not TAG_RE.match(commit):
        raise Refuse(
            f"_commit {commit!r} is not a release tag (vX.Y.Z); copier cannot rebuild the old "
            "version from the template. Report it as-is and ask the maintainer"
        )
    if not src_path_ok(read):
        src = answer_value(read(".copier-answers.yml"), "_src_path")
        raise Refuse(
            f"_src_path {src!r} is not {REPO} (or its old name {OFFICIAL_REPOS[1]}) on GitHub "
            f"(a local clone or a fork?). On "
            f"{BRANCH_PREFIX}{tag}: replace the existing `_src_path` line in .copier-answers.yml with `_src_path: gh:{REPO}` (edit it, do not add a second line), commit "
            f"(copier update refuses a dirty tree), run this check again, then "
            f"`copier update --vcs-ref {tag} --defaults --conflict inline`"
        )
    check_annotated(tag, source)


# --- branch, push, markers, labels, changelog ----------------------------------------------


def default_branch(root: Path) -> str:
    out = git(root, "ls-remote", "--symref", "origin", "HEAD")
    m = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", out, re.MULTILINE)
    if not m:
        raise Refuse("cannot determine origin's default branch")
    return m[1]


def compute_status(root: Path, source: str, assume: str | None) -> dict:
    git(root, "fetch", "-q", "origin")
    base = f"origin/{default_branch(root)}"
    kind, current = current_version(ref_reader(root, base), assume)
    latest = latest_tag(source)
    out = {
        "kind": kind,
        "current": current,
        "latest": latest,
        "base": base,
        "state": compare_versions(current, latest),
    }
    if kind == "generated":
        out["src_ok"] = src_path_ok(ref_reader(root, base))
    return out


def create_branch(root: Path, tag: str) -> str:
    parse_tag(tag)
    name = BRANCH_PREFIX + tag
    dirty = dirty_entries(root)
    if dirty:
        raise Refuse("working tree is not clean:\n  " + "\n  ".join(dirty))
    if git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False).strip():
        raise Refuse(f"branch {name} already exists locally")
    base = default_branch(root)
    if git(root, "ls-remote", "--heads", "origin", name).strip():
        raise Refuse(f"branch {name} already exists on origin")
    git(root, "fetch", "-q", "origin", base)
    git(root, "switch", "-q", "-c", name, "--no-track", f"origin/{base}")
    return name


def push_args(name: str) -> list[str]:
    return ["push", "-u", "origin", f"refs/heads/{name}:refs/heads/{name}"]


def push_branch(root: Path, tag: str) -> str:
    name = BRANCH_PREFIX + tag
    parse_tag(tag)
    if current_branch(root) != name:
        raise Refuse(f"push only {name}, and only from it")
    git(root, *push_args(name))
    return name


def find_markers(root: Path) -> list[str]:
    out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    hits = []
    for rel in (p for p in out.split("\0") if p):
        if rel.endswith(".rej"):
            hits.append(f"reject file: {rel}")
            continue
        p = root / rel
        if p.is_symlink() or not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        hits += [
            f"conflict marker: {rel}:{i}" for i, ln in enumerate(lines, 1) if MARKER_RE.match(ln)
        ]
    return hits


def parse_labels(bootstrap: str) -> list[str]:
    m = re.search(r"^LABELS=\(([^)\n]*)\)\s*$", bootstrap, re.MULTILINE)
    if not m:
        raise Refuse("no LABELS=(...) line in the tag's bootstrap.sh")
    names = m[1].split()
    for n in names:
        if not LABEL_RE.match(n):
            raise Refuse(f"label name {n!r} from the tag does not look like a label; not creating")
    return names


def missing_labels(wanted: list[str], existing: list[str]) -> list[str]:
    have = {e.lower() for e in existing}
    return [w for w in wanted if w.lower() not in have]


def plan_labels(missing: Lines, shadowing: bool) -> tuple[Lines, Lines]:
    """Split missing labels into (to create, skipped). A shadowing repo skips Actions-only ones."""
    if not shadowing:
        return list(missing), []
    return (
        [m for m in missing if m not in ACTIONS_ONLY_LABELS],
        [m for m in missing if m in ACTIONS_ONLY_LABELS],
    )


def label_lines(to_create: Lines, skipped: Lines) -> Lines:
    """What `labels` prints: never nothing, so an empty result is not mistaken for a failure."""
    lines = list(to_create) if to_create else ["none missing"]
    return lines + [f"skipped (Actions-only label, not needed by the skill): {n}" for n in skipped]


def changelog_entries(text: str, frm: str, to: str) -> list[str]:
    lo, hi = parse_tag(frm), parse_tag(to)
    entries: list[Entry] = []
    for line in text.splitlines():
        m = re.match(r"^- (v\d+\.\d+\.\d+|Unreleased)\b", line)
        if m:
            entries.append((m[1], [line]))
        elif entries:
            entries[-1][1].append(line)
    return [
        "\n".join(body) for ver, body in entries if TAG_RE.match(ver) and lo < parse_tag(ver) <= hi
    ]


# --- CLI ------------------------------------------------------------------------------------


def _gh_repo(root: Path, override: str | None) -> str:
    if override:
        return override
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode or not proc.stdout.strip():
        raise Refuse("cannot determine this repo's name with gh")
    return proc.stdout.strip()


def run(args: argparse.Namespace) -> int:
    root = repo_root(args.repo)
    cmd = args.cmd
    if cmd == "preflight":
        print(json.dumps(preflight(root)))
    elif cmd == "latest":
        print(latest_tag(args.source))
    elif cmd == "status":
        preflight(root)
        st = compute_status(root, args.source, args.assume_tag)
        print(json.dumps(st))
        if st["state"] == "up-to-date":
            print(f"already on {st['latest']}: nothing to do", file=sys.stderr)
    elif cmd == "branch":
        preflight(root)
        print(create_branch(root, args.tag))
    elif cmd == "verify-local":
        _, current = current_version(tree_reader(root), args.assume_tag)
        with tempfile.TemporaryDirectory() as tmp:
            fetch_tag(current, Path(tmp) / "t", args.source)
            tree = Path(tmp) / "t"
            verify_local(root, tree, current)
            names = folder_names(tree, strict=False)
            for name in names:
                if not exists_locally(root, name):
                    print(f"would re-add (absent locally): {name}")
        print(f"ok: the skill folders match {current}")
    elif cmd == "apply":
        preflight(root)
        notes: list[str] = []
        try:
            written = apply_sync(root, args.source, args.to_tag, args.assume_tag, notes)
        finally:
            for note in notes:
                print(note)
        for rel in written:
            print(rel)
    elif cmd == "changelog":
        with tempfile.TemporaryDirectory() as tmp:
            fetch_tag(args.tag, Path(tmp) / "t", args.source)
            text = (Path(tmp) / "t" / "CHANGELOG.md").read_text(encoding="utf-8")
        # JSON, so nothing in the quoted text can end a fence or pose as the agent's own output
        entries = changelog_entries(text, args.frm, args.tag)
        print(json.dumps({"data_not_instructions": True, "entries": entries}, indent=2))
    elif cmd == "labels":
        with tempfile.TemporaryDirectory() as tmp:
            fetch_tag(args.tag, Path(tmp) / "t", args.source)
            boot = (Path(tmp) / "t" / "template/scripts/bootstrap.sh.jinja").read_text("utf-8")
        wanted = parse_labels(boot)
        repo = _gh_repo(root, args.gh_repo)
        proc = subprocess.run(
            [
                "gh",
                "label",
                "list",
                "-R",
                repo,
                "--limit",
                "500",
                "--json",
                "name",
                "-q",
                ".[].name",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode:
            raise Refuse(f"gh label list failed: {proc.stderr.strip()}")
        missing = missing_labels(wanted, proc.stdout.split("\n"))
        shadowing = not (root / ".copier-answers.yml").is_file()
        to_create, skipped = plan_labels(missing, shadowing)
        created: list[str] = []
        for name in to_create:
            if args.create:
                done = subprocess.run(  # never --force: it would overwrite existing labels
                    ["gh", "label", "create", name, "-R", repo],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if done.returncode:
                    raise Refuse(
                        f"gh label create {name} failed ({done.stderr.strip()}); "
                        f"created so far: {created or 'none'}"
                    )
                created.append(name)
        for line in label_lines(to_create, skipped):
            print(line)
    elif cmd == "copier-check":
        if preflight(root)["kind"] != "generated":
            raise Refuse("copier-check is for generated repos")
        copier_check(root, args.tag, args.source)
        print(f"ok: {args.tag} is annotated, _commit is a release tag, _src_path is {REPO}")
    elif cmd == "check-markers":
        hits = find_markers(root)
        if hits:
            raise Refuse("do not commit these:\n  " + "\n  ".join(hits))
        print("ok: no conflict markers or .rej files")
    elif cmd == "push":
        print(push_branch(root, args.tag))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--self-test", action="store_true", help="run offline checks and exit")
    p.add_argument("--repo", default=".", help="the downstream repo (default: cwd)")
    p.add_argument("--source", default=DEFAULT_SOURCE, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("preflight")
    sub.add_parser("latest")
    sub.add_parser("check-markers")
    for name in ("status", "verify-local"):
        sub.add_parser(name).add_argument("--assume-tag")
    sub.add_parser("branch").add_argument("tag")
    sub.add_parser("push").add_argument("tag")
    sub.add_parser("copier-check").add_argument("tag")
    ap = sub.add_parser("apply")
    ap.add_argument("--to-tag", required=True)
    ap.add_argument("--assume-tag")
    cl = sub.add_parser("changelog")
    cl.add_argument("--tag", required=True)
    cl.add_argument("--from", dest="frm", required=True)
    lb = sub.add_parser("labels")
    lb.add_argument("--tag", required=True)
    lb.add_argument("--create", action="store_true")
    lb.add_argument("--gh-repo", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.self_test:
        from_self_test()
        return 0
    if not args.cmd:
        p.error("a command is required")
    try:
        return run(args)
    except Refuse as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1


# --- self-test: temp dirs and local bare repos only, no network ------------------------------

SKILL = ".claude/skills/factory"
SYNCF = ".claude/skills/sync-root-factory"


def _w(root: Path, rel: str, text: str, mode: int | None = None) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mode:
        p.chmod(mode)


def _snap(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.relative_to(root).parts:
            continue
        out[p.relative_to(root).as_posix()] = p.read_bytes() if p.is_file() else None
    return out


def _tpl(
    version: str,
    skill_text: str,
    with_version: bool,
    with_sync: bool,
    folders: dict[str, str] | None = None,
) -> dict[str, str]:
    files = {
        f"template/{SKILL}/SKILL.md": skill_text,
        f"template/{SKILL}/scripts/x.py": f"print('x {version}')\n",
        f"template/{SKILL}/references/gates.md": f"gates {version}\n",
        "CHANGELOG.md": (
            f"- Unreleased: pending\n- v{version} (d): entry for {version}\n  - sub {version}\n"
            "- v0.1.0 (d): first\n"
        ),
        "template/scripts/bootstrap.sh.jinja": "LABELS=(factory:review improvement P0 documentation)\n",
    }
    if with_version:
        files[f"template/{SKILL}/VERSION"] = f"{version}\n"
    if with_sync:
        files[f"template/{SYNCF}/SKILL.md"] = f"sync {version}\n"
    for name, text in (folders or {}).items():  # more skill folders, same fixture
        files[f"template/{SKILLS}/{name}/SKILL.md"] = text
    return files


def from_self_test() -> None:
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    for k, v in {"NAME": "t", "EMAIL": "t@example.invalid"}.items():
        os.environ.setdefault(f"GIT_AUTHOR_{k}", v)
        os.environ.setdefault(f"GIT_COMMITTER_{k}", v)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _self_test(Path(tmp))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("sync.py self-test: ok")


def _refuses(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except Refuse as exc:
        return str(exc)
    raise AssertionError(f"{fn.__name__}{a} should have been refused")


def _self_test(tmp: Path) -> None:
    def g(root: Path, *a: str) -> str:
        return git(root, *a)

    def commit_all(root: Path, msg: str) -> None:
        g(root, "add", "-A")
        g(root, "commit", "-q", "-m", msg)

    # the fake template: v0.1.0 (no VERSION), v0.2.0 (VERSION + sync skill), main differs
    src = tmp / "srcwork"
    src.mkdir()
    g(src, "init", "-q", "-b", "main")
    for ver, with_v, with_s in (("0.1.0", False, False), ("0.2.0", True, True)):
        for rel, text in _tpl(ver, f"skill {ver}\n", with_v, with_s).items():
            _w(src, rel, text)
        commit_all(src, ver)
        g(src, "tag", "-a", f"v{ver}", "-m", f"v{ver}")
    _w(src, f"template/{SKILL}/SKILL.md", "MAIN ONLY, never copy\n")
    _w(src, "MAIN_ONLY", "x\n")
    commit_all(src, "main moves on")
    bare = tmp / "src.git"
    g(tmp, "clone", "-q", "--bare", str(src), str(bare))
    source = str(bare)

    assert latest_tag(source) == "v0.2.0"
    # a lightweight tag and a symlink tag, pushed after the latest-tag check
    g(src, "tag", "v0.3.0")
    g(src, "checkout", "-q", "-b", "sym", "v0.2.0")
    (src / f"template/{SKILL}/link").symlink_to("../../../../elsewhere")
    commit_all(src, "symlink")
    g(src, "tag", "-a", "v0.8.0", "-m", "v0.8.0")
    g(src, "checkout", "-q", "main")
    badsrc = tmp / "bad.git"  # a second source that also has the lightweight and symlink tags
    g(tmp, "clone", "-q", "--bare", str(src), str(badsrc))
    g(src, "push", "-q", str(badsrc), "v0.3.0", "v0.8.0")

    # downstream: a shadowing repo holding v0.1.0's skill, with an origin
    origin = tmp / "origin.git"
    g(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp / "seed"
    g(tmp, "clone", "-q", str(origin), str(seed))
    g(seed, "checkout", "-q", "-b", "main")
    for rel, text in _tpl("0.1.0", "skill 0.1.0\n", False, False).items():
        if rel.startswith(f"template/{SKILL}"):
            _w(seed, rel[len("template/") :], text)
    _w(seed, ".gitignore", "__pycache__/\n")
    _w(seed, ".claude/commands/keep.md", "sentinel: not ours to touch\n")
    _w(seed, "CLAUDE.md", "mine\n")
    commit_all(seed, "downstream at v0.1.0")
    g(seed, "push", "-q", "-u", "origin", "main")

    def clone(name: str) -> Path:
        d = tmp / name
        g(tmp, "clone", "-q", str(origin), str(d))
        return d

    down = clone("down")

    # --- pure helpers
    assert compare_versions("v0.1.0", "v0.2.0") == "behind"
    assert compare_versions("v0.2.0", "v0.2.0") == "up-to-date"
    _refuses(compare_versions, "v0.9.0", "v0.2.0")  # downgrade
    _refuses(compare_versions, "v0.6.0-3-gabc123", "v0.7.0")  # describe string
    for bad in ("main", "v1.2", "v1.2.3/../x", "v0.1.0 ", "", "1.2.3"):
        _refuses(parse_tag, bad)
    assert current_version(lambda p: "_commit: v0.6.0\n" if "copier" in p else None, None) == (
        "generated",
        "v0.6.0",
    )
    _refuses(current_version, lambda p: None, None)  # no VERSION, no --assume-tag
    assert current_version(lambda p: None, "v0.1.0") == ("shadowing", "v0.1.0")
    _refuses(current_version, lambda p: "0.2.0\n" if p == VERSION_FILE else None, "v0.1.0")
    _refuses(current_version, lambda p: "banana\n" if p == VERSION_FILE else None, None)
    assert parse_labels("LABELS=(a:b P0 x_y.z)\n") == ["a:b", "P0", "x_y.z"]
    _refuses(parse_labels, "LABELS=(ok $(rm) P0)\n")
    _refuses(parse_labels, "LABELS=(-rf)\n")
    assert missing_labels(["a", "P0", "b"], ["A", "p0", "zzz"]) == ["b"]
    assert plan_labels(["P0", "factory:design", "design:approved"], True) == (
        ["P0"],
        ["factory:design", "design:approved"],
    )
    # #77: the progress labels the skill sets are created, never skipped
    assert plan_labels(["factory:designing", "factory:design", "factory:building"], True) == (
        ["factory:designing", "factory:building"],
        ["factory:design"],
    )
    assert plan_labels(["P0", "factory:design"], False) == (["P0", "factory:design"], [])
    assert "--force" not in " ".join(push_args("local/adopt-v0.2.0"))
    assert not any(a.startswith("+") for a in push_args("local/adopt-v0.2.0"))

    # --- preflight: dirty (untracked counts), amplifactory itself
    assert preflight(down)["kind"] == "shadowing"
    _w(down, "stray.txt", "x")
    _refuses(preflight, down)
    (down / "stray.txt").unlink()
    fake = tmp / "fake-af"
    fake.mkdir()
    g(fake, "init", "-q", "-b", "main")
    _w(fake, "copier.yml", "x: 1\n")
    _w(fake, "template/a", "x")
    _refuses(preflight, fake)
    byurl = tmp / "by-url"
    byurl.mkdir()
    g(byurl, "init", "-q", "-b", "main")
    g(byurl, "remote", "add", "origin", f"git@github.com:{REPO}.git")
    assert is_template_repo(byurl) and not is_template_repo(down)
    byold = tmp / "by-old-url"
    byold.mkdir()
    g(byold, "init", "-q", "-b", "main")
    g(byold, "remote", "add", "origin", "git@github.com:rahmansunny071/agent-factory.git")
    assert is_template_repo(byold)  # the old name, still the template repo

    # --- status: no VERSION needs --assume-tag; downgrade and wrong-kind refused
    _refuses(compute_status, down, source, None)
    assert compute_status(down, source, "v0.1.0")["state"] == "behind"
    _refuses(compute_status, down, source, "v0.9.0")
    assert compute_status(down, source, "v0.2.0")["state"] == "up-to-date"

    # --- branch: cut from origin's tip, not from local commits; existing branches refused
    other = clone("other")
    _w(other, "ORIGIN_ONLY", "o\n")
    commit_all(other, "origin moves")
    g(other, "push", "-q", "origin", "main")
    g(other, "push", "-q", "origin", "main:refs/heads/local/adopt-v0.3.0")
    _w(down, "LOCAL_ONLY", "l\n")
    commit_all(down, "unpushed")
    before = _snap(down)
    _refuses(create_branch, down, "v0.2.0/../x")
    _refuses(create_branch, down, "v0.3.0")  # exists on origin
    assert _snap(down) == before and current_branch(down) == "main"
    assert create_branch(down, "v0.2.0") == "local/adopt-v0.2.0"
    assert (down / "ORIGIN_ONLY").exists() and not (down / "LOCAL_ONLY").exists()
    g(down, "switch", "-q", "main")
    _refuses(create_branch, down, "v0.2.0")  # exists locally
    g(down, "switch", "-q", "local/adopt-v0.2.0")

    # --- fetch: tag only, annotated only, no links
    t2 = tmp / "t2"
    files = fetch_tag("v0.2.0", t2, source)
    assert f"template/{SKILL}/SKILL.md" in files and not (t2 / "MAIN_ONLY").exists()
    assert (t2 / f"template/{SKILL}/SKILL.md").read_text() == "skill 0.2.0\n"
    for bad_tag in ("v0.3.0", "v0.8.0", "v9.9.9", "main"):
        dest = tmp / f"bad-{bad_tag}"
        _refuses(fetch_tag, bad_tag, dest, str(badsrc))
        assert not dest.exists() or not any(dest.iterdir())
    _refuses(fetch_tag, "v0.2.0", t2, source)  # dest not empty
    t1 = tmp / "t1"
    fetch_tag("v0.1.0", t1, source)

    # --- verify-local: clean match, then every way to differ
    assert differences(down, t1) == []
    sk = down / SKILL / "SKILL.md"
    sk.write_text("local edit\n")
    msg = _refuses(verify_local, down, t1, "v0.1.0")
    assert f"modified: {SKILL}/SKILL.md" in msg
    g(down, "checkout", "--", ".")
    assert differences(down, t2), "a wrong claimed tag must show differences"
    _refuses(verify_local, down, t2, "v0.2.0")
    _w(down, f"{SKILL}/scripts/new.py", "untracked\n")
    assert "untracked" in _refuses(verify_local, down, t1, "v0.1.0")
    (down / SKILL / "scripts/new.py").unlink()
    down2 = clone("down2")
    (down2 / ".claude").rename(tmp / "elsewhere")
    (down2 / ".claude").symlink_to(tmp / "elsewhere")
    assert "symlink" in _refuses(differences, down2, t1)

    # --- apply
    # not on an adopt branch / dirty / downgrade / wrong claim: refused, nothing written
    before = _snap(down2)
    _refuses(apply_sync, down2, source, "v0.2.0", "v0.1.0")
    assert _snap(down2) == before
    _w(down, "stray.txt", "x")
    before = _snap(down)
    _refuses(apply_sync, down, source, "v0.2.0", "v0.1.0")  # dirty
    assert _snap(down) == before, "a refused apply must leave the tree unchanged"
    (down / "stray.txt").unlink()
    _refuses(apply_sync, down, source, "v0.2.0", None)  # no VERSION, no --assume-tag
    _refuses(apply_sync, down, source, "v0.2.0", "v0.2.0")  # not newer
    _refuses(apply_sync, down, source, "v0.8.0", "v0.1.0")  # wrong branch name for the tag
    (down / SKILL / "SKILL.md").write_text("local edit\n")
    commit_all(down, "local edit")
    snap_edit = _snap(down)
    msg = _refuses(apply_sync, down, source, "v0.2.0", "v0.1.0")  # local edits vs claimed tag
    assert "modified" in msg and _snap(down) == snap_edit
    g(down, "revert", "--no-edit", "HEAD")
    # a gitignored symlink at a folder the OLD tag lacks must not be written through
    # (the tree reads clean, so only the apply-side symlink guard stops this)
    down4 = clone("down4")
    create_branch(down4, "v0.2.0")
    outside = tmp / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("PRECIOUS\n")
    with (down4 / ".gitignore").open("a") as fh:
        fh.write(f"{SYNCF}\n")
    commit_all(down4, "ignore the shared skill checkout")
    (down4 / SYNCF).symlink_to(outside)
    assert dirty_entries(down4) == [], "the ignored symlink must not make the tree dirty"
    assert "symlink" in _refuses(apply_sync, down4, source, "v0.2.0", "v0.1.0")
    assert sorted(p.name for p in outside.iterdir()) == ["SKILL.md"]
    assert (outside / "SKILL.md").read_text() == "PRECIOUS\n"
    assert "outside" in _refuses(check_no_symlinks, down4, "../outside")
    # a release that forgot to bump VERSION is refused before any write
    g(src, "checkout", "-q", "-b", "badver", "v0.2.0")
    _w(src, f"template/{SKILL}/VERSION", "0.2.1\n")
    commit_all(src, "wrong VERSION")
    g(src, "tag", "-a", "v0.4.0", "-m", "v0.4.0")
    g(src, "checkout", "-q", "main")
    g(src, "push", "-q", str(badsrc), "v0.4.0")
    down3 = clone("down3")
    create_branch(down3, "v0.4.0")
    before = _snap(down3)
    assert "forgot to bump" in _refuses(apply_sync, down3, str(badsrc), "v0.4.0", "v0.1.0")
    assert _snap(down3) == before
    # a clean apply: sentinel untouched, ignored files survive, VERSION arrives
    _w(down, f"{SKILL}/scripts/__pycache__/x.pyc", "cache")
    sentinel = (down / ".claude/commands/keep.md").read_bytes()
    claude_md = (down / "CLAUDE.md").read_bytes()
    written = apply_sync(down, source, "v0.2.0", "v0.1.0")
    assert f"{SKILL}/VERSION" in written and f"{SYNCF}/SKILL.md" in written
    assert (down / ".claude/commands/keep.md").read_bytes() == sentinel
    assert (down / "CLAUDE.md").read_bytes() == claude_md
    assert (down / SKILL / "scripts/__pycache__/x.pyc").exists()
    assert post_diff(down, t2, [SKILL, SYNCF]) == []
    assert (down / SKILL / "VERSION").read_text() == "0.2.0\n"
    assert (down / SKILL / "SKILL.md").read_text() == "skill 0.2.0\n"

    # --- markers, push, changelog
    assert find_markers(down) == []
    _w(down, "CLAUDE.md", "a\n" + "<" * 7 + " HEAD\nb\n" + "=" * 7 + "\nc\n" + ">" * 7 + " tpl\n")
    assert len(find_markers(down)) == 2
    _w(down, "CLAUDE.md", "mine\n")
    _w(down, "README.md.rej", "x")
    assert any(".rej" in h for h in find_markers(down))
    (down / "README.md.rej").unlink()
    commit_all(down, "adopt v0.2.0")
    _refuses(push_branch, down, "v0.1.0")
    assert push_branch(down, "v0.2.0") == "local/adopt-v0.2.0"
    heads = g(down, "ls-remote", "--heads", "origin")
    assert "refs/heads/local/adopt-v0.2.0" in heads
    entries = changelog_entries((t2 / "CHANGELOG.md").read_text(), "v0.1.0", "v0.2.0")
    assert len(entries) == 1 and "sub 0.2.0" in entries[0] and "pending" not in entries[0]
    assert changelog_entries((t2 / "CHANGELOG.md").read_text(), "v0.2.0", "v0.2.0") == []
    _self_test_folders(tmp, g, commit_all)


def _self_test_folders(tmp: Path, g, commit_all) -> None:
    """#50: apply syncs every skill folder the tag ships; #48: copier-check."""
    m = tmp / "multi"
    m.mkdir()
    g(m, "init", "-q", "-b", "main")
    tags = {
        # v0.1.0: factory, factory-lesson and a skill v0.2.0 drops
        "0.1.0": {"factory-lesson": "lesson 0.1.0\n", "dropped": "dropped 0.1.0\n"},
        "0.2.0": {
            "factory-lesson": "lesson 0.2.0\n",
            "new-skill": "new 0.2.0\n",
            "zz-new": "zz 0.2.0\n",
        },
        "0.3.0": {"factory-lesson": "lesson 0.3.0\n", "Bad_Name": "x\n"},
        "0.5.0": {"factory-lesson": "lesson 0.5.0\n"},
    }
    for ver, extra in tags.items():
        shutil.rmtree(m / "template", ignore_errors=True)
        for rel, text in _tpl(ver, f"skill {ver}\n", ver != "0.1.0", False, extra).items():
            _w(m, rel, text)
        if ver == "0.5.0":
            _w(m, f"template/{SKILL}/Local.md", "tpl\n")
        if ver == "0.2.0":  # a plain file directly under skills/ is never written downstream
            _w(m, f"template/{SKILLS}/README.md", "not a folder\n")
        commit_all(m, ver)
        g(m, "tag", "-a", f"v{ver}", "-m", f"v{ver}")
    g(m, "tag", "v0.4.0")  # lightweight
    mbare = tmp / "multi.git"
    g(tmp, "clone", "-q", "--bare", str(m), str(mbare))
    msrc = str(mbare)

    def down_at(name: str, to: str, extra: dict[str, str] | None = None, ignore: str = "") -> Path:
        """A shadowing repo holding v0.1.0's skills (+ extra files), on local/adopt-<to>."""
        o = tmp / f"{name}-origin.git"
        g(tmp, "init", "-q", "--bare", "-b", "main", str(o))
        d = tmp / name
        g(tmp, "clone", "-q", str(o), str(d))
        g(d, "checkout", "-q", "-b", "main")
        for rel, text in _tpl("0.1.0", "skill 0.1.0\n", False, False, tags["0.1.0"]).items():
            if rel.startswith("template/"):
                _w(d, rel[len("template/") :], text)
        _w(d, ".gitignore", "__pycache__/\n" + ignore)
        _w(d, ".claude/skills/my-own/SKILL.md", "my own skill\n")
        for rel, text in (extra or {}).items():
            _w(d, rel, text)
        commit_all(d, "downstream")
        g(d, "push", "-q", "-u", "origin", "main")
        create_branch(d, to)
        return d

    def refused_unchanged(d: Path, to: str, want: str) -> None:
        before = _snap(d)
        assert want in _refuses(apply_sync, d, msrc, to, "v0.1.0")
        assert _snap(d) == before, "a refused apply must leave every folder unchanged"

    # names and "exists" come from the filesystem
    assert folder_names(tmp / "nowhere", strict=True) == []
    probe = tmp / "probe"
    (probe / SKILLS).mkdir(parents=True)
    assert not exists_locally(probe, "x-skill")
    (probe / SKILLS / "x-skill").symlink_to(tmp / "no-such-target")  # dangling
    assert exists_locally(probe, "x-skill")
    (probe / SKILLS / "Case-Skill").mkdir()
    assert exists_locally(probe, "case-skill")
    real_lexists = os.path.lexists  # a case-sensitive filesystem must give the same answer
    os.path.lexists = lambda _p: False  # type: ignore[assignment]
    try:
        assert exists_locally(probe, "case-skill") and not exists_locally(probe, "other")
    finally:
        os.path.lexists = real_lexists

    # the pensieve case: a tracked hand copy of a folder the OLD tag ships. Replaced after verify;
    # new folders added; the user's own skill untouched; a folder v0.2.0 drops left alone (even
    # modified); a plain file under skills/ ignored.
    d = down_at("real", "v0.2.0")
    _w(d, f"{SKILLS}/dropped/SKILL.md", "dropped, edited locally\n")
    commit_all(d, "edit the folder v0.2.0 drops")
    own = (d / SKILLS / "my-own/SKILL.md").read_bytes()
    dropped = (d / SKILLS / "dropped/SKILL.md").read_bytes()
    notes: list[str] = []
    written = apply_sync(d, msrc, "v0.2.0", "v0.1.0", notes)
    assert f"{SKILLS}/new-skill/SKILL.md" in written and f"{SKILLS}/zz-new/SKILL.md" in written
    assert (d / SKILLS / "factory-lesson/SKILL.md").read_text() == "lesson 0.2.0\n"
    assert (d / SKILL / "VERSION").read_text() == "0.2.0\n"
    assert (d / SKILLS / "my-own/SKILL.md").read_bytes() == own
    assert (d / SKILLS / "dropped/SKILL.md").read_bytes() == dropped
    assert not (d / SKILLS / "README.md").exists()
    assert any("no longer shipped by v0.2.0, left alone: dropped" in n for n in notes)

    # old tag lacked it, it exists locally: refused, whatever it is, and `factory/` (sorts first)
    # is still unchanged because every folder is checked before the first write
    d = down_at("lack1", "v0.2.0", {f"{SKILLS}/zz-new/SKILL.md": "mine\n"})
    refused_unchanged(d, "v0.2.0", "did not ship it")
    d = down_at("lack2", "v0.2.0")
    _w(d, f"{SKILLS}/zz-new/SKILL.md", "untracked\n")
    (d / ".gitignore").write_text("__pycache__/\n.claude/skills/zz-new/\n")
    commit_all(d, "ignore it")  # now zz-new is gitignored and untracked
    assert dirty_entries(d) == []
    refused_unchanged(d, "v0.2.0", "did not ship it")
    d = down_at("lack3", "v0.2.0", {f"{SKILLS}/ZZ-New/SKILL.md": "case variant\n"})
    refused_unchanged(d, "v0.2.0", "did not ship it")
    d = down_at("lack4", "v0.2.0", ignore=f"{SKILLS}/zz-new\n")
    (d / SKILLS / "zz-new").symlink_to(tmp / "no-such-target")  # ignored dangling symlink
    assert dirty_entries(d) == []
    refused_unchanged(d, "v0.2.0", "symlink")
    d = down_at("lack5", "v0.2.0", ignore=f"{SKILLS}/new-skill/\n")
    refused_unchanged(d, "v0.2.0", "gitignored")  # would be written but never committed

    # old tag had it: modified or partly deleted refuses; wholly absent is re-added and said so
    d = down_at("mod", "v0.2.0")
    _w(d, f"{SKILLS}/factory-lesson/SKILL.md", "edited\n")
    commit_all(d, "edit")
    refused_unchanged(d, "v0.2.0", "modified")
    d = down_at("part", "v0.2.0", {f"{SKILLS}/factory-lesson/extra.md": "e\n"})
    g(d, "rm", "-q", f"{SKILLS}/factory-lesson/SKILL.md")
    g(d, "commit", "-q", "-m", "partly delete")
    refused_unchanged(d, "v0.2.0", "missing locally")
    d = down_at("gone", "v0.2.0")
    g(d, "rm", "-q", "-r", f"{SKILLS}/factory-lesson")
    g(d, "commit", "-q", "-m", "delete the folder")
    t1m = tmp / "t1m"
    fetch_tag("v0.1.0", t1m, msrc)
    assert not any("factory-lesson" in x for x in differences(d, t1m))  # absent: nothing to compare
    notes = []
    apply_sync(d, msrc, "v0.2.0", "v0.1.0", notes)
    assert "re-adding (absent locally): factory-lesson" in notes
    assert (d / SKILLS / "factory-lesson/SKILL.md").read_text() == "lesson 0.2.0\n"

    # never write over a path git does not track, even when the gitignore check is fooled
    # (core.ignorecase=false on a case-insensitive filesystem): isolate the lexists guard
    d = down_at("untracked", "v0.5.0", ignore=f"{SKILL}/Local.md\n")
    _w(d, f"{SKILL}/Local.md", "PRECIOUS\n")
    assert dirty_entries(d) == []
    real_ignored = globals()["_is_ignored"]
    globals()["_is_ignored"] = lambda _r, _p: False
    try:
        refused_unchanged(d, "v0.5.0", "does not track")
    finally:
        globals()["_is_ignored"] = real_ignored
    assert (d / SKILL / "Local.md").read_text() == "PRECIOUS\n"

    # `.claude/skills` is a regular file: a refusal, not a traceback
    d = down_at("skillsfile", "v0.2.0")
    g(d, "rm", "-q", "-r", SKILLS)
    _w(d, SKILLS, "i am a file\n")
    commit_all(d, "skills is a file")
    refused_unchanged(d, "v0.2.0", "is a file")

    # a tag with no skill folders is no basis for "ok"
    t0 = tmp / "t0"
    _w(t0, "CHANGELOG.md", "- v0.0.5 (d): first\n")
    d = down_at("vacuous", "v0.2.0")
    assert "nothing to compare" in _refuses(verify_local, d, t0, "v0.0.5")

    # a tag with an unsafe folder name refuses everything
    d = down_at("badname", "v0.3.0")
    refused_unchanged(d, "v0.3.0", "unsafe names")

    # labels: never silent
    assert label_lines([], []) == ["none missing"]
    assert label_lines([], ["factory:design"]) == [
        "none missing",
        "skipped (Actions-only label, not needed by the skill): factory:design",
    ]
    assert label_lines(["P0"], []) == ["P0"]

    # copier-check (#48)
    def answers(src: str | None, commit: str = "v0.1.0") -> str:
        return (f"_src_path: {src}\n" if src is not None else "") + f"_commit: {commit}\n"

    def ok(text: str) -> bool:
        return src_path_ok(lambda p: text if p == ".copier-answers.yml" else None)

    for name in OFFICIAL_REPOS:  # every case holds for the new and the old name (#67)
        short = name.split("/")[1]
        for good in (
            f"gh:{name}",
            f"'gh:{name}'",
            f'"https://github.com/{name}.git"',
            f"https://github.com/{name}/",
            f"git@github.com:{name}.git",
            f"ssh://git@github.com/{name}.git/",
            f"gh:{name.upper()} # a comment",
        ):
            assert ok(answers(good)), good
        for bad in (
            f"/Users/me/{short}",
            f"../{short}",
            f"file:///tmp/{name}",
            f"https://gitlab.com/{name}",
            f"git+https://github.com/{name}",
            f"gh:someone-else/{short}",
            f"gh:{name}-fork",
            f"https://evilgithub.com/{name}",
            "",
        ):
            assert not ok(answers(bad)), bad
        for bad in (  # the scheme and alias are case-sensitive in Copier; `.git` and `/` strip once
            f"GH:{name}",
            f"HTTPS://GITHUB.COM/{name}",
            f"gh:{name}.git.git",
            f"https://github.com/{name}/.git",
            f"gh:{name}//",
        ):
            assert not ok(answers(bad)), bad
        assert ok(answers(f"gh:{name.upper()}.git/"))  # the owner/repo part is case-insensitive
    assert not ok(answers(None)) and not ok("")
    # a repeated key: Copier keeps the LAST, so the first match proves nothing
    dup_good_first = answers(f"gh:{REPO}") + "_src_path: /Users/me/agent-factory-clone\n"
    dup_bad_first = "_src_path: /Users/me/agent-factory-clone\n" + answers(f"gh:{REPO}")
    assert not ok(dup_good_first) and not ok(dup_bad_first)
    gen = tmp / "gen"
    gen.mkdir()
    g(gen, "init", "-q", "-b", "main")
    _w(gen, "README.md", "x\n")
    _w(gen, ".copier-answers.yml", answers(f"gh:{REPO}"))
    commit_all(gen, "gen")
    copier_check(gen, "v0.2.0", msrc)
    _refuses(copier_check, gen, "v0.2", msrc)  # not vX.Y.Z
    _refuses(copier_check, gen, "v0.4.0", msrc)  # lightweight tag
    _refuses(copier_check, gen, "v9.9.9", msrc)  # no such tag
    # an adopter still on the old name passes end to end, and `status` reports src_ok (#67)
    old = f"gh:{OFFICIAL_REPOS[1]}"
    _w(gen, ".copier-answers.yml", answers(old))
    commit_all(gen, "old name")
    copier_check(gen, "v0.2.0", msrc)
    genorigin = tmp / "gen-origin.git"
    g(tmp, "init", "-q", "--bare", "-b", "main", str(genorigin))
    g(gen, "remote", "add", "origin", str(genorigin))
    g(gen, "push", "-q", "origin", "main")
    assert compute_status(gen, msrc, None)["src_ok"] is True
    _w(gen, ".copier-answers.yml", answers(f"gh:{REPO}"))
    commit_all(gen, "new name")
    _w(gen, ".copier-answers.yml", answers(f"gh:{REPO}", "v0.1.0-3-gabc123"))
    assert "release tag" in _refuses(copier_check, gen, "v0.2.0", msrc)  # describe string
    _w(gen, ".copier-answers.yml", answers(str(tmp / "amplifactory-clone")))
    assert "_src_path" in _refuses(copier_check, gen, "v0.2.0", msrc)
    _w(gen, ".copier-answers.yml", answers(None))
    _refuses(copier_check, gen, "v0.2.0", msrc)
    for dup in (dup_good_first, dup_bad_first, answers(f"gh:{REPO}") + "_commit: v0.1.0\n"):
        _w(gen, ".copier-answers.yml", dup)
        msg = _refuses(copier_check, gen, "v0.2.0", msrc)
        assert "more than once" in msg and "replace it" in msg


if __name__ == "__main__":
    sys.exit(main())
