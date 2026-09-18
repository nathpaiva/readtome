"""End to end tests for cli.py, with a fake `say` on the PATH."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli     # noqa: E402
import parse   # noqa: E402

DOC = """\
# First heading

One sentence that is long enough to stand on its own here.

## Second heading

Another sentence that is long enough to stand on its own here.
"""


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.doc = self.root / "doc.md"
        self.doc.write_text(DOC)

        fake = self.root / "say"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.root}{os.pathsep}{self.old_path}"

        os.environ["READTOME_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        os.environ.pop("READTOME_STATE_DIR", None)
        self.tmp.cleanup()

    def run_cli(self, *argv, reader=input):
        out = io.StringIO()
        err = io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = cli.main(list(argv), reader=reader)
        finally:
            sys.stdout, sys.stderr = old
        return code, out.getvalue(), err.getvalue()

    def spy_on_play(self):
        """Patch speak.play so a test can read the rate and start it got.

        Playing for real would need a fake `say` and take time. A spy is
        enough, since these tests only check what main() worked out to
        pass in, not what play() does with it.
        """
        seen = {}

        def spy(blocks, **kwargs):
            seen.update(kwargs)
            return kwargs.get("start", 0)

        original = cli.speak.play
        cli.speak.play = spy
        self.addCleanup(setattr, cli.speak, "play", original)
        return seen


class ListTest(CliTestCase):
    def test_list_prints_the_headings_with_their_lines(self):
        code, out, _ = self.run_cli(str(self.doc), "--list")
        self.assertEqual(code, 0)
        self.assertIn("First heading", out)
        self.assertIn("Second heading", out)
        self.assertIn("5", out)

    def test_list_prints_no_paragraph(self):
        _, out, _ = self.run_cli(str(self.doc), "--list")
        self.assertNotIn("long enough", out)


class FindStartTest(unittest.TestCase):
    def setUp(self):
        self.blocks = parse.parse(DOC)

    def test_no_value_starts_at_the_top(self):
        self.assertEqual(cli.find_start(self.blocks, None), 0)

    def test_a_number_starts_at_that_line(self):
        self.assertEqual(self.blocks[cli.find_start(self.blocks, "5")].line, 5)

    def test_a_number_on_a_blank_line_takes_the_next_block(self):
        self.assertEqual(self.blocks[cli.find_start(self.blocks, "4")].line, 5)

    def test_text_matches_a_heading_without_case(self):
        index = cli.find_start(self.blocks, "second head")
        self.assertEqual(self.blocks[index].sentences, ["Second heading"])

    def test_a_number_past_the_end_is_an_error(self):
        with self.assertRaises(cli.locate.LocateError) as cm:
            cli.find_start(self.blocks, "9999")
        self.assertIn("9999", str(cm.exception))

    def test_text_that_matches_nothing_is_an_error(self):
        with self.assertRaises(cli.locate.LocateError):
            cli.find_start(self.blocks, "no such heading")


class ExitCodeTest(CliTestCase):
    def test_a_good_run_returns_zero(self):
        code, _, _ = self.run_cli(str(self.doc))
        self.assertEqual(code, 0)

    def test_a_missing_file_returns_one_and_says_why(self):
        code, _, err = self.run_cli(str(self.root / "nope.md"))
        self.assertEqual(code, 1)
        self.assertIn("nope.md", err)

    def test_a_bad_from_value_returns_one(self):
        code, _, err = self.run_cli(str(self.doc), "--from", "no such heading")
        self.assertEqual(code, 1)
        self.assertIn("no such heading", err)

    def test_list_works_without_say(self):
        # Listing headings needs no audio, so it has to work on a machine
        # where `say` does not exist.
        os.environ["PATH"] = "/nonexistent"
        code, out, _ = self.run_cli(str(self.doc), "--list")
        self.assertEqual(code, 0)
        self.assertIn("First heading", out)

    def test_a_missing_say_returns_one(self):
        # A RuntimeError from check_say is a different road to exit 1 than a
        # LocateError, and it was the one no test ever drove.
        os.environ["PATH"] = "/nonexistent"
        code, _, err = self.run_cli(str(self.doc))
        self.assertEqual(code, 1)
        self.assertIn("macOS", err)

    def test_a_file_with_nothing_to_read_returns_one(self):
        empty = self.root / "empty.md"
        empty.write_text("<!-- only a comment -->\n")
        code, _, err = self.run_cli(str(empty))
        self.assertEqual(code, 1)
        self.assertIn("nothing to read", err)

    def test_ctrl_c_returns_130(self):
        # 130 is in the contract, so it gets a test. Raising from play is the
        # deterministic way to reach it.
        def boom(*args, **kwargs):
            raise KeyboardInterrupt

        original = cli.speak.play
        cli.speak.play = boom
        self.addCleanup(setattr, cli.speak, "play", original)
        code, _, _ = self.run_cli(str(self.doc))
        self.assertEqual(code, 130)


class FakeReader:
    """Stands in for input(). Hands back one answer per prompt."""

    def __init__(self, *answers):
        self.pending = list(answers)

    def __call__(self, prompt=""):
        return self.pending.pop(0) if self.pending else ""


class Terminal(io.StringIO):
    """A stream that claims to be a terminal, the way a real one would."""

    def isatty(self):
        return True


class UseFzfTest(unittest.TestCase):
    """The four things that have to hold before fzf draws the menu."""

    def setUp(self):
        self.old_available = cli.menu.available
        cli.menu.available = lambda: True
        self.addCleanup(setattr, cli.menu, "available", self.old_available)
        stdin = sys.stdin
        sys.stdin = Terminal()
        self.addCleanup(setattr, sys, "stdin", stdin)

    def test_yes_when_all_four_hold(self):
        self.assertTrue(cli.use_fzf(Terminal(), input))

    def test_no_when_the_reader_is_not_the_real_input(self):
        self.assertFalse(cli.use_fzf(Terminal(), FakeReader("1")))

    def test_no_when_the_output_is_not_a_terminal(self):
        self.assertFalse(cli.use_fzf(io.StringIO(), input))

    def test_no_when_stdin_is_not_a_terminal(self):
        sys.stdin = io.StringIO()
        self.assertFalse(cli.use_fzf(Terminal(), input))

    def test_no_when_fzf_is_not_installed(self):
        cli.menu.available = lambda: False
        self.assertFalse(cli.use_fzf(Terminal(), input))


class ChooseUsesFzfTest(unittest.TestCase):
    def setUp(self):
        self.asked = []
        self.old = cli.use_fzf, cli.menu.pick
        cli.menu.pick = lambda title, rows: self.asked.append((title, rows)) or 1
        self.addCleanup(self.restore)

    def restore(self):
        cli.use_fzf, cli.menu.pick = self.old

    def test_it_hands_the_rows_to_fzf_when_use_fzf_says_so(self):
        cli.use_fzf = lambda stream, reader: True
        stream = Terminal()
        self.assertEqual(cli.choose("branches", ["a", "b"], stream), 1)
        self.assertEqual(self.asked, [("branches", ["a", "b"])])
        # The numbered list would sit behind fzf and stay in the scrollback.
        self.assertEqual(stream.getvalue(), "")

    def test_it_prints_the_numbered_menu_when_use_fzf_says_no(self):
        cli.use_fzf = lambda stream, reader: False
        stream = Terminal()
        picked = cli.choose("branches", ["a", "b"], stream, FakeReader("2"))
        self.assertEqual(picked, 1)
        self.assertEqual(self.asked, [])
        self.assertIn("  2  b", stream.getvalue())


class ChooseTest(unittest.TestCase):
    def test_a_number_picks_that_row(self):
        out = io.StringIO()
        self.assertEqual(cli.choose("t", ["a", "b", "c"], out, FakeReader("2")), 1)

    def test_enter_alone_picks_the_first(self):
        out = io.StringIO()
        self.assertEqual(cli.choose("t", ["a", "b"], out, FakeReader("")), 0)

    def test_q_cancels(self):
        out = io.StringIO()
        self.assertIsNone(cli.choose("t", ["a", "b"], out, FakeReader("q")))

    def test_one_row_skips_the_menu(self):
        out = io.StringIO()
        self.assertEqual(cli.choose("t", ["only"], out, FakeReader()), 0)
        self.assertEqual(out.getvalue(), "")

    def test_a_number_out_of_range_is_an_error(self):
        out = io.StringIO()
        with self.assertRaises(cli.locate.LocateError):
            cli.choose("t", ["a", "b"], out, FakeReader("9"))


class StateTest(CliTestCase):
    def test_the_state_file_follows_the_environment_variable(self):
        self.assertTrue(str(cli.state_path()).startswith(str(self.root / "state")))

    def test_a_position_survives_a_round_trip(self):
        cli.save_position("k", 7, 260)
        self.assertEqual(cli.load_position("k"), (7, 260))

    def test_an_unknown_key_gives_nothing(self):
        self.assertIsNone(cli.load_position("never saved"))

    def test_a_broken_state_file_is_ignored(self):
        path = cli.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertIsNone(cli.load_position("k"))


class ResumeTest(CliTestCase):
    def test_resume_starts_where_it_stopped(self):
        self.run_cli(str(self.doc))                       # plays to the end
        saved = cli.load_position(str(self.doc.resolve()))
        self.assertIsNotNone(saved)
        code, _, _ = self.run_cli(str(self.doc), "--resume")
        self.assertEqual(code, 0)

    def test_from_beats_resume(self):
        # This has to drive main(). The precedence lives there, and a test
        # that calls find_start directly gives the same answer whichever order
        # main applies the two flags in, so it proves nothing.
        cli.save_position(str(self.doc.resolve()), 3, 220)
        seen = {}

        def spy(blocks, **kwargs):
            seen.update(kwargs)
            return kwargs.get("start", 0)

        original = cli.speak.play
        cli.speak.play = spy
        self.addCleanup(setattr, cli.speak, "play", original)

        self.run_cli(str(self.doc), "--from", "1", "--resume")
        self.assertEqual(seen["start"], 0)


class RatePrecedenceTest(CliTestCase):
    def test_resume_restores_the_saved_rate(self):
        cli.save_position(str(self.doc.resolve()), 0, 300)
        seen = self.spy_on_play()
        self.run_cli(str(self.doc), "--resume")
        self.assertEqual(seen["rate"], 300)

    def test_an_explicit_rate_beats_the_saved_one(self):
        # -r with the default number is the hard case: argparse cannot tell
        # "typed it" from "left it out" unless the default is a sentinel.
        cli.save_position(str(self.doc.resolve()), 0, 300)
        seen = self.spy_on_play()
        self.run_cli(str(self.doc), "-r", str(cli.DEFAULT_RATE), "--resume")
        self.assertEqual(seen["rate"], cli.DEFAULT_RATE)

    def test_resume_starts_at_the_saved_block(self):
        # The old test only checked the exit code, so a resume that always
        # started at the top would have passed it.
        cli.save_position(str(self.doc.resolve()), 2, 220)
        seen = self.spy_on_play()
        self.run_cli(str(self.doc), "--resume")
        self.assertEqual(seen["start"], 2)

    def test_saving_leaves_no_temp_file_behind(self):
        cli.save_position("k", 1, 220)
        self.assertEqual(list(cli.state_path().parent.glob("*.tmp")), [])


class MenuSourceTest(CliTestCase):
    """The path a bare `readtome` takes. It had no test at all."""

    def git(self, *args, when=None):
        env = dict(os.environ)
        if when:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def setUp(self):
        super().setUp()
        self.repo = self.root / "demo"
        (self.repo / "docs").mkdir(parents=True)
        subprocess.run(["git", "-C", str(self.root), "init", "-q", "demo"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "Test")
        self.git("checkout", "-q", "-b", "main")
        # Two files on purpose. With one, `choose` takes its single row
        # shortcut and never draws the menu, so a test looking for the menu
        # finds nothing at all. The dates are pinned so the newer file is
        # row 1 every run, the same rule as the locate tests.
        (self.repo / "docs" / "two.md").write_text(
            "# Another doc\n\nText that is long enough to stand alone here.\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "older", when="2026-09-01T10:00:00")
        (self.repo / "docs" / "one.md").write_text(DOC)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "newer", when="2026-09-02T10:00:00")

    def test_a_project_with_no_file_argument_uses_the_menu(self):
        code, out, _ = self.run_cli("-p", str(self.repo), "--list",
                                    reader=FakeReader("1"))
        self.assertEqual(code, 0)
        self.assertIn("markdown in main", out)     # the menu was drawn
        self.assertIn("docs/one.md", out)
        self.assertIn("docs/two.md", out)
        self.assertIn("First heading", out)        # and row 1 was the one read

    def test_a_menu_with_no_terminal_says_so(self):
        # This lives here, not on CliTestCase, because it needs a REAL repo
        # with two files. Without one, find_project raises first and choose is
        # never reached, so the test passes while proving nothing.
        def no_terminal(prompt=""):
            raise EOFError

        code, _, err = self.run_cli("-p", str(self.repo), reader=no_terminal)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertIn("needs a terminal", err)

    def test_the_same_file_gets_one_key_either_way(self):
        # Opened by path and picked from the menu on the current branch, the
        # same document has to keep one saved place, not two.
        by_path = cli.resolve_source(
            cli.build_parser().parse_args([str(self.repo / "docs" / "one.md")]))
        by_menu = cli.resolve_source(
            cli.build_parser().parse_args(["-p", str(self.repo)]),
            stream=io.StringIO(), reader=FakeReader("1"))
        self.assertEqual(by_path[2], by_menu[2])

    def test_the_key_changes_with_the_branch(self):
        # One path holds different text on different branches, so a saved
        # place has to be filed per branch or the two overwrite each other.
        args = cli.build_parser().parse_args([str(self.repo / "docs" / "one.md")])
        on_main = cli.resolve_source(args)[2]
        self.git("checkout", "-q", "-b", "other")
        on_other = cli.resolve_source(args)[2]
        self.assertNotEqual(on_main, on_other)
        self.assertTrue(on_main.endswith("@main"))
        self.assertTrue(on_other.endswith("@other"))

    def test_a_file_outside_a_repo_keys_by_path_alone(self):
        loose = self.root / "loose.md"
        loose.write_text(DOC)
        key = cli.resolve_source(cli.build_parser().parse_args([str(loose)]))[2]
        self.assertNotIn("@", key)


class ProgressTest(CliTestCase):
    # CliTestCase, not TestCase. It puts the fake `say` on the PATH. Without
    # it this test calls the real macOS `say` and the machine talks out loud
    # on every run of the suite, which no assertion notices.
    def test_play_reports_the_rate_it_ended_on(self):
        import speak
        progress = {}
        speak.play([parse.Block(kind="prose", line=1, sentences=["Only one."])],
                   rate=220, interactive=False, progress=progress)
        self.assertEqual(progress["rate"], 220)
        self.assertEqual(progress["block"], 0)


class RepoTest(unittest.TestCase):
    """Things about the checkout itself, not about one function."""

    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_cli_is_executable(self):
        # The install symlinks straight at this file, so a file without the
        # bit set would fail only at first run.
        self.assertTrue(os.access(self.repo / "cli.py", os.X_OK))

    def test_the_readme_documents_every_flag(self):
        # Compared against the parser, not a list written by hand, so a flag
        # added later turns this red. And the flag has to sit in a TABLE ROW,
        # not merely appear somewhere in the file: a flag named only in a
        # usage example is mentioned, not documented, and this test says
        # documented.
        rows = [line for line
                in (self.repo / "README.md").read_text().splitlines()
                if line.startswith("|")]
        for action in cli.build_parser()._actions:
            for flag in action.option_strings:
                if flag in ("-h", "--help"):
                    continue
                self.assertTrue(any(f"`{flag}`" in row for row in rows),
                                f"{flag} is in no table row of the README")


if __name__ == "__main__":
    unittest.main()
