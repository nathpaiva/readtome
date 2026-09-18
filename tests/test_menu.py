"""Tests for readtome/menu.py.

Most tests put a fake fzf on the PATH, so they are fast and do not draw on the
screen. The last one runs the real fzf on a real pty and presses the arrow
key, because an injected key proves the decision and not the device.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import struct
import termios
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import menu   # noqa: E402

REPO = str(Path(__file__).resolve().parents[1])

UP = b"\x1b[A"
DOWN = b"\x1b[B"
ENTER = b"\r"

ROWS = [
    "feature-login-page             2 hours ago      wt",
    "feature-login-page-tests       16 hours ago     wt",
    "bugfix-the-empty-cart          17 hours ago     wt",
]


class FakeFzfTestCase(unittest.TestCase):
    """A fake fzf on the PATH. It saves what it was given and answers to order."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.given = self.root / "given.txt"

    def fake_fzf(self, picks: str = "", code: int = 0):
        """Write a fake fzf that prints `picks` and exits with `code`."""
        fake = self.root / "fzf"
        fake.write_text(
            "#!/bin/sh\n"
            f'cat > "{self.given}"\n'
            # %b, not %s: the fake answers with a tab in the line, the same
            # way fzf does, and %s would print a backslash and a t.
            f'printf "%b" "{picks}"\n'
            f"exit {code}\n"
        )
        fake.chmod(0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = f"{self.root}{os.pathsep}{old}"
        self.addCleanup(os.environ.__setitem__, "PATH", old)

    def lines_given(self) -> list[str]:
        return self.given.read_text().splitlines()


class PickTest(FakeFzfTestCase):
    def test_it_returns_the_index_of_the_chosen_row(self):
        self.fake_fzf(picks="2\\tbugfix-the-empty-cart")
        self.assertEqual(menu.pick("branches", ROWS), 2)

    def test_every_row_reaches_fzf_with_its_index_in_front(self):
        self.fake_fzf(picks="0\\tx")
        menu.pick("branches", ROWS)
        self.assertEqual(self.lines_given(),
                         [f"{i}\t{row}" for i, row in enumerate(ROWS)])

    def test_two_rows_reading_the_same_still_return_the_chosen_one(self):
        # Without the hidden index, fzf hands back text, and looking that text
        # up in the list would always find the first of the pair.
        self.fake_fzf(picks="1\\tsame")
        self.assertEqual(menu.pick("branches", ["same", "same"]), 1)

    def test_a_tab_inside_a_row_does_not_shift_the_index(self):
        self.fake_fzf(picks="1\\tb c")
        self.assertEqual(menu.pick("branches", ["a", "b\tc"]), 1)
        self.assertEqual(self.lines_given(), ["0\ta", "1\tb c"])

    def test_esc_cancels(self):
        self.fake_fzf(code=130)
        self.assertIsNone(menu.pick("branches", ROWS))

    def test_nothing_matching_cancels(self):
        self.fake_fzf(code=1)
        self.assertIsNone(menu.pick("branches", ROWS))

    def test_no_answer_cancels(self):
        self.fake_fzf(picks="", code=0)
        self.assertIsNone(menu.pick("branches", ROWS))

    def test_a_real_fzf_failure_is_not_swallowed(self):
        # Code 2 is fzf's own error. Treating it as a cancel would read the
        # wrong file, or none, and never say why.
        self.fake_fzf(code=2)
        with self.assertRaises(RuntimeError) as caught:
            menu.pick("branches", ROWS)
        self.assertIn("2", str(caught.exception))


class AvailableTest(FakeFzfTestCase):
    def test_true_when_fzf_is_on_the_path(self):
        self.fake_fzf()
        self.assertTrue(menu.available())

    def test_false_when_it_is_not(self):
        old = os.environ["PATH"]
        os.environ["PATH"] = str(self.root)   # empty folder, no fzf
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        self.assertFalse(menu.available())


CHILD = """
import json, sys
sys.path.insert(0, {repo!r})
import menu
got = menu.pick("branches", {rows!r})
open({out!r}, "w").write(json.dumps(got))
"""


def _take_the_terminal():
    """Make the pty this process's controlling terminal, so fzf finds /dev/tty."""
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


@unittest.skipUnless(shutil.which("fzf"), "fzf is not installed")
class RealFzfTest(unittest.TestCase):
    """The real fzf, on a real pty, with the arrow key really pressed."""

    def drive(self, keys, rows=ROWS, timeout=20.0):
        """Open the menu, press the keys, return what was picked."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "picked.json"
        source = CHILD.format(repo=REPO, rows=rows, out=str(out))

        leader, follower = pty.openpty()
        # A pty starts with no size at all, and fzf asks the terminal how tall
        # it is before it draws. Without this it draws nothing and waits.
        fcntl.ioctl(follower, termios.TIOCSWINSZ,
                    struct.pack("HHHH", 30, 100, 0, 0))
        proc = subprocess.Popen([sys.executable, "-c", source], stdin=follower,
                                stdout=follower, stderr=follower,
                                preexec_fn=_take_the_terminal)
        os.close(follower)
        self.addCleanup(proc.kill)
        self.addCleanup(os.close, leader)

        deadline = time.time() + timeout
        seen = ""

        def drain(until_gone=False):
            nonlocal seen
            ready, _, _ = select.select([leader], [], [], 0.2)
            if not ready:
                return
            try:
                data = os.read(leader, 4096)
            except OSError:      # the child closed the pty
                return
            seen += data.decode("utf-8", "replace")

        while rows[-1] not in seen:
            self.assertLess(time.time(), deadline,
                            f"fzf never drew the rows. Screen: {seen!r}")
            drain()

        for key in keys:
            os.write(leader, key)
            # fzf redraws between key presses. Reading the screen is what
            # lets it get there before the next one lands.
            settle = time.time() + 0.3
            while time.time() < settle:
                drain()

        while proc.poll() is None:
            self.assertLess(time.time(), deadline, "the child never exited")
            drain()
        proc.wait(timeout=5)
        return json.loads(out.read_text())

    def test_the_up_arrow_walks_the_list(self):
        # fzf's default layout, which her own `pick` also uses, puts the first
        # row at the bottom. Up is the key that walks into the list.
        self.assertEqual(self.drive([UP, ENTER]), 1)

    def test_enter_alone_takes_the_row_fzf_starts_on(self):
        self.assertEqual(self.drive([ENTER]), 0)

    def test_the_hidden_index_is_not_something_you_can_type_at(self):
        # The index rides in a first column that `--with-nth 2..` keeps off
        # the screen. None of these rows holds a digit, so typing one can only
        # match the hidden column. It must find nothing and cancel.
        rows = ["alpha branch", "beta branch", "gamma branch"]
        self.assertIsNone(self.drive([b"1", ENTER], rows=rows))


if __name__ == "__main__":
    unittest.main()
