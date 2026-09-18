"""Ask the person to choose one row, through fzf.

fzf is what her own `pick` shell function already uses, so the menu behaves
the way her hands expect: arrows move, typing filters, enter picks, esc
cancels. It stays optional. With no fzf installed, `cli.choose` keeps its
numbered menu, so readtome still needs nothing but the standard library.
"""

from __future__ import annotations

import shutil
import subprocess

# The index rides in a hidden first column. `--with-nth 2..` keeps it off the
# screen, and it comes back on the chosen line, so two rows reading the same
# still return the one the cursor was on.
FLAGS = [
    "--delimiter", "\t",
    "--with-nth", "2..",
    "--no-multi",
    "--height", "80%",
    "--border",
]

CANCELLED = (1, 130)   # fzf: 1 is nothing matched, 130 is esc or ctrl-c


def available() -> bool:
    return shutil.which("fzf") is not None


def pick(title: str, rows: list[str]) -> int | None:
    """Run fzf over the rows. Returns the chosen index, or None to cancel."""
    # A tab inside a row would fake a column break and shift the index.
    lines = "\n".join(f"{i}\t{row.replace(chr(9), ' ')}"
                      for i, row in enumerate(rows))
    done = subprocess.run(["fzf", *FLAGS, "--prompt", f"{title} > "],
                          input=lines, text=True, stdout=subprocess.PIPE)
    if done.returncode in CANCELLED:
        return None
    if done.returncode != 0:
        raise RuntimeError(f"fzf stopped with code {done.returncode}")
    chosen = done.stdout.strip("\n")
    if not chosen:
        return None
    return int(chosen.split("\t", 1)[0])
