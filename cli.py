#!/usr/bin/env python3
"""readtome: read a markdown file out loud.

Run it with no argument inside a repo and it offers the markdown files on the
current branch. Give it a path and it reads that file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import locate   # noqa: E402
import menu     # noqa: E402
import parse    # noqa: E402
import speak    # noqa: E402

DEFAULT_RATE = 220


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="readtome",
        description="Read a markdown file out loud with macOS say.",
        epilog="Keys while playing: space pauses, \u2193 and \u2191 move one "
               "sentence, n and b jump between headings, + and - (or = and _) "
               "change the speed, q quits.")
    p.add_argument("file", nargs="?", help="a markdown file. Beats every other flag.")
    p.add_argument("-p", "--project", help="repo name or path")
    p.add_argument("-b", "--branch", help="branch to read from")
    p.add_argument("-B", dest="pick_branch", action="store_true",
                   help="show the branches and pick one")
    p.add_argument("-r", "--rate", type=int, default=None,
                   help=f"words per minute (default {DEFAULT_RATE})")
    p.add_argument("--from", dest="start_at", metavar="TEXT_OR_LINE",
                   help="start at a heading, or at a line number")
    p.add_argument("--list", dest="show_list", action="store_true",
                   help="print the headings with their lines and stop")
    p.add_argument("--code", dest="read_code", action="store_true",
                   help="read the code blocks instead of skipping them")
    p.add_argument("--voice", help="one macOS voice, turns language detection off")
    p.add_argument("--resume", action="store_true",
                   help="continue where this file stopped last time")
    return p


def find_start(blocks: list[parse.Block], start_at: str | None) -> int:
    """Turn --from into a block index."""
    if not start_at:
        return 0

    if start_at.isdigit():
        wanted = int(start_at)
        for i, block in enumerate(blocks):
            if block.line >= wanted:
                return i
        # A heading that matches nothing is an error, so a line past the end
        # is one too. Rewinding to the top without a word is the wrong answer
        # to the same kind of mistake.
        raise locate.LocateError(
            f"no block at or after line {wanted}. "
            f"The file ends at line {blocks[-1].line}.")

    needle = start_at.lower()
    for i, block in enumerate(blocks):
        if block.kind == "heading" and needle in " ".join(block.sentences).lower():
            return i
    raise locate.LocateError(f"no heading matches '{start_at}'. Try --list.")


def format_list(blocks: list[parse.Block]) -> str:
    return "\n".join(f"{b.line:>4}  {'#' * b.level} {b.sentences[0]}"
                     for b in blocks if b.kind == "heading")


def use_fzf(stream, reader) -> bool:
    """Whether to draw the menu with fzf instead of printing numbers.

    All four have to hold. A test or a pipe hands over its own reader, and
    `input` is the only one a person is behind. Both ends have to be a
    terminal, since fzf draws on one and reads from the other. And fzf has
    to be installed at all, because readtome does not require it.
    """
    return (reader is input
            and stream.isatty()
            and sys.stdin.isatty()
            and menu.available())


def choose(title: str, rows: list[str], stream=None, reader=input) -> int | None:
    """Pick one row. fzf on a terminal, a numbered menu anywhere else."""
    stream = stream if stream is not None else sys.stdout
    if not rows:
        raise locate.LocateError(f"nothing to choose for {title}")
    if len(rows) == 1:
        return 0

    if use_fzf(stream, reader):
        return menu.pick(title, rows)

    print(title, file=stream)
    for number, row in enumerate(rows, start=1):
        print(f"  {number}  {row}", file=stream)

    try:
        answer = reader("> ").strip()
    except EOFError:
        # No terminal to ask. A menu needs an answer, so say what is missing
        # instead of raising a traceback at the user.
        raise locate.LocateError(
            "this needs a terminal to show the menu. "
            "Pass a file path, or use -b to name a branch.")
    if answer.lower() == "q":
        return None
    if not answer:
        return 0
    if not answer.isdigit() or not 1 <= int(answer) <= len(rows):
        raise locate.LocateError(f"'{answer}' is not a number between 1 and {len(rows)}")
    return int(answer) - 1


def state_path() -> Path:
    """Where the resume positions live.

    READTOME_STATE_DIR moves it. Tests set that, so a test run never writes
    into the real state folder.
    """
    base = os.environ.get("READTOME_STATE_DIR")
    folder = Path(base).expanduser() if base else Path.home() / ".local" / "state" / "readtome"
    return folder / "positions.json"


def _read_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}                       # a broken file is not worth an error
    return data if isinstance(data, dict) else {}


def load_position(key: str) -> tuple[int, int] | None:
    entry = _read_state().get(key)
    if not isinstance(entry, dict):
        return None
    try:
        return int(entry["block"]), int(entry["rate"])
    except (KeyError, TypeError, ValueError):
        return None


def save_position(key: str, block: int, rate: int) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_state()
    data[key] = {"block": block, "rate": rate}
    # Write beside the real file, then move it into place. A half written
    # positions.json reads back as "no history", which would throw away every
    # other document's saved place and not just this one.
    # The pid is in the name so two processes saving at once cannot rename
    # each other's file out from under themselves.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _key_for(path: Path) -> str:
    """The name a saved position is filed under.

    A document is its path AND the branch it was read on, because the working
    copy at one path holds different text on different branches. A file that
    sits outside any repo has no branch and keys by path alone.
    """
    target = Path(path).expanduser()
    try:
        repo = locate.find_project(None, cwd=str(target.parent))
        return f"{target.resolve()}@{locate.current_branch(repo)}"
    except locate.LocateError:
        return str(target.resolve())


def resolve_source(args, stream=None, reader=input):
    """Turn the flags into (label, text, state key), or None when cancelled."""
    stream = stream if stream is not None else sys.stdout
    if args.file:
        target = Path(args.file).expanduser()
        # read_path first, so a missing file still raises its own message.
        return str(target), locate.read_path(args.file), _key_for(target)

    repo = locate.find_project(args.project, cwd=os.getcwd())

    if args.pick_branch:
        branches = locate.list_branches(repo)
        rows = [f"{b.name:<30} {b.when:<15}{'  wt' if b.has_worktree else ''}"
                for b in branches]
        picked = choose(f"branches in {repo}", rows, stream, reader)
        if picked is None:
            return None
        branch = branches[picked].name
    else:
        branch = args.branch or locate.current_branch(repo)

    files = locate.list_markdown(repo, branch)
    picked = choose(f"markdown in {branch}", files, stream, reader)
    if picked is None:
        return None
    relpath = files[picked]
    # Always path plus branch, the same shape _key_for builds for a plain
    # path argument. One document opened either way on one branch shares one
    # saved place, and two branches keep two.
    full = repo / relpath
    return (f"{relpath} @ {branch}",
            locate.read_file(repo, branch, relpath),
            f"{full}@{branch}")


def main(argv: list[str] | None = None, reader=input) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = resolve_source(args, reader=reader)
        if source is None:
            return 0                                  # the user pressed q
        label, text, key = source

        blocks = parse.parse(text, read_code=args.read_code)
        if not blocks:
            print(f"nothing to read in {label}", file=sys.stderr)
            return 1

        if args.show_list:
            print(format_list(blocks))
            return 0

        # Checked here, not at the top: listing headings needs no audio, so it
        # has to work on a machine with no `say`.
        speak.check_say()

        rate = args.rate if args.rate is not None else DEFAULT_RATE
        start = 0
        if args.resume:
            saved = load_position(key)
            if saved:
                start, saved_rate = saved
                if args.rate is None:
                    rate = saved_rate             # an explicit -r still wins
        if args.start_at:
            start = find_start(blocks, args.start_at)

        progress: dict = {}
        speak.play(blocks, rate=rate, voice=args.voice, start=start,
                   progress=progress)
        save_position(key, progress.get("block", start), progress.get("rate", rate))
        return 0
    except locate.LocateError as err:
        print(str(err), file=sys.stderr)
        return 1
    except RuntimeError as err:
        print(str(err), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
