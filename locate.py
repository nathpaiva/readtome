"""Work out which markdown file to read, and get its text.

The rules here are git's rules, so this module shells out to git instead of
holding its own idea of what a branch is.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class LocateError(Exception):
    """Something the user can fix, reported as one line and exit 1."""


@dataclass
class BranchInfo:
    name: str
    when: str            # "2 hours ago", straight from git
    has_worktree: bool   # true when the branch is checked out somewhere


def _git(repo: Path | str, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args],
                                capture_output=True, text=True)
    except FileNotFoundError:
        # No git on this machine at all. Say so instead of raising a
        # traceback. `_key_for` catches this, so reading a file by path keeps
        # working; only picking one by project or branch needs git.
        raise LocateError("git was not found. readtome needs it to pick a "
                          "file by project or branch. Pass a file path "
                          "instead.")
    if result.returncode != 0:
        raise LocateError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _roots() -> list[Path]:
    raw = os.environ.get("READTOME_ROOTS", "")
    return [Path(p).expanduser() for p in raw.split(":") if p]


def find_project(name: str | None, cwd: str) -> Path:
    """Turn a name, a path, or nothing at all into a repo folder."""
    if name:
        candidate = Path(name).expanduser()
        if candidate.is_dir():
            return Path(_git(candidate, "rev-parse", "--show-toplevel").strip())

        roots = _roots()
        if not roots:
            raise LocateError(
                f"cannot look up '{name}': READTOME_ROOTS is not set. "
                "Set it to the folders that hold your repos, "
                "for example READTOME_ROOTS=~/code:~/work")
        for root in roots:
            if (root / name).is_dir():
                return Path(_git(root / name, "rev-parse", "--show-toplevel").strip())
        listed = ", ".join(str(r) for r in roots)
        raise LocateError(f"no repo called '{name}' under: {listed}")

    try:
        return Path(_git(cwd, "rev-parse", "--show-toplevel").strip())
    except LocateError as err:
        # Only one git failure earns the friendly message. A folder that
        # cannot be entered says "cannot change to ...", and hiding that
        # behind "not inside a git repo" sends the user to the wrong place.
        if "not a git repository" in str(err).lower():
            raise LocateError(f"{cwd} is not inside a git repo. "
                              "Pass a file path, or use -p to name a project.") from err
        raise


def current_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def _checked_out(repo: Path) -> set[str]:
    out = _git(repo, "worktree", "list", "--porcelain")
    return {line.split("refs/heads/", 1)[1].strip()
            for line in out.splitlines()
            if line.startswith("branch refs/heads/")}


def list_branches(repo: Path) -> list[BranchInfo]:
    """Local branches, the one with the newest commit first."""
    out = _git(repo, "for-each-ref", "--sort=-committerdate", "refs/heads",
               "--format=%(refname:short)\t%(committerdate:relative)")
    live = _checked_out(repo)
    branches = []
    for line in out.splitlines():
        name, _, when = line.partition("\t")
        branches.append(BranchInfo(name=name, when=when, has_worktree=name in live))
    return branches


RECENT_COMMITS = 50   # how far back to look when ordering the list

# git says one of these two when the path is simply not there. Any other
# failure, a branch that does not exist for instance, is a different problem
# and keeps git's own words.
MISSING_PATH = ("does not exist in", "exists on disk, but not in")


def list_markdown(repo: Path, branch: str) -> list[str]:
    """Every committed .md file on that branch, useful ones first.

    Reading the list from git solves the noise problem for free. A file under
    node_modules was never committed, so it never appears, and there is no
    ignore list to keep in sync.
    """
    tracked = [p for p in _git(repo, "ls-tree", "-r", "--name-only", branch).splitlines()
               if p.endswith(".md")]
    if not tracked:
        raise LocateError(f"no markdown file on branch '{branch}'. Try -B to pick another.")

    known = set(tracked)
    recent: list[str] = []
    log = _git(repo, "log", f"-{RECENT_COMMITS}", "--name-only",
               "--pretty=format:", branch, "--", "*.md")
    for path in log.splitlines():
        path = path.strip()
        if path in known and path not in recent:
            recent.append(path)

    rest = sorted(p for p in tracked if p not in recent)
    return recent + rest


def read_file(repo: Path, branch: str, relpath: str) -> str:
    """Text of one file on one branch.

    On the current branch the file on disk wins, so an edit that is not
    committed yet is the one being read.
    """
    if branch == current_branch(repo):
        on_disk = repo / relpath
        if on_disk.is_file():
            return on_disk.read_text(encoding="utf-8")
    try:
        return _git(repo, "show", f"{branch}:{relpath}")
    except LocateError as err:
        if any(marker in str(err).lower() for marker in MISSING_PATH):
            raise LocateError(f"'{relpath}' is not on branch '{branch}'") from err
        raise


def read_path(path: str) -> str:
    """Text of a plain file, with no git involved."""
    target = Path(path).expanduser()
    if not target.is_file():
        raise LocateError(f"no such file: {path}")
    return target.read_text(encoding="utf-8")
