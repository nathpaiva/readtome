"""Tests for readtome/locate.py.

These build a real git repo in a temp folder. git is not mocked: the rules
being tested are git's own rules, and a mock would only test the mock.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import locate  # noqa: E402


def git(repo, *args, when=None):
    """Run one git command. `when` pins the commit date.

    `list_branches` sorts by commit date, and two commits made in the same
    second sort in an arbitrary order. Measured without a pinned date: four
    runs out of five put the branches the wrong way round. So the dates are
    pinned, and the order test means something.
    """
    env = dict(os.environ)
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_repo(root: Path) -> Path:
    repo = root / "demo"
    (repo / "docs").mkdir(parents=True)
    git(root, "init", "-q", "demo")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "checkout", "-q", "-b", "main")
    (repo / ".gitignore").write_text("node_modules/\n")
    (repo / "README.md").write_text("# on main\n")
    (repo / "docs" / "one.md").write_text("# one on main\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "junk.md").write_text("# never listed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "first", when="2026-09-01T10:00:00")

    git(repo, "checkout", "-q", "-b", "side")
    (repo / "docs" / "two.md").write_text("# two on side\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "second", when="2026-09-02T10:00:00")
    git(repo, "checkout", "-q", "main")
    return repo


class LocateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Resolved on purpose. `git rev-parse --show-toplevel` always resolves
        # symlinks, and on macOS a temp folder sits under /var, which is a
        # symlink to /private/var. Without this, every path check below
        # compares two names for one folder and fails.
        self.root = Path(self.tmp.name).resolve()
        self.repo = make_repo(self.root)

    def tearDown(self):
        self.tmp.cleanup()


class FindProjectTest(LocateTestCase):
    def test_a_path_argument_resolves_to_its_repo_root(self):
        self.assertEqual(locate.find_project(str(self.repo), cwd="/"), self.repo)

    def test_a_name_is_looked_up_in_the_roots(self):
        os.environ["READTOME_ROOTS"] = str(self.root)
        self.addCleanup(os.environ.pop, "READTOME_ROOTS", None)
        self.assertEqual(locate.find_project("demo", cwd="/"), self.repo)

    def test_no_name_uses_the_repo_of_the_current_folder(self):
        self.assertEqual(locate.find_project(None, cwd=str(self.repo / "docs")),
                         self.repo)

    def test_a_name_with_no_roots_says_so(self):
        os.environ.pop("READTOME_ROOTS", None)
        with self.assertRaises(locate.LocateError) as cm:
            locate.find_project("demo", cwd="/")
        self.assertIn("READTOME_ROOTS", str(cm.exception))

    def test_a_folder_outside_git_says_so(self):
        with self.assertRaises(locate.LocateError):
            locate.find_project(None, cwd=str(self.root))

    def test_a_different_git_failure_keeps_its_own_message(self):
        # "this is not a repo" and "I cannot enter that folder" are different
        # problems. Reporting both the same way sends the user looking in the
        # wrong place. Measured: git answers "cannot change to ...: No such
        # file or directory" here, which carries no "not a git repository".
        missing = self.root / "does-not-exist"
        with self.assertRaises(locate.LocateError) as cm:
            locate.find_project(None, cwd=str(missing))
        self.assertNotIn("not inside a git repo", str(cm.exception))
        self.assertIn("cannot change to", str(cm.exception).lower())

    def test_a_name_not_under_the_roots_lists_them(self):
        os.environ["READTOME_ROOTS"] = str(self.root)
        self.addCleanup(os.environ.pop, "READTOME_ROOTS", None)
        with self.assertRaises(locate.LocateError) as cm:
            locate.find_project("no-such-repo", cwd="/")
        self.assertIn(str(self.root), str(cm.exception))

    def test_no_git_at_all_says_so(self):
        # A machine with no git must get a message, not a traceback.
        original = locate.subprocess.run

        def no_git(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "git")

        locate.subprocess.run = no_git
        self.addCleanup(setattr, locate.subprocess, "run", original)
        with self.assertRaises(locate.LocateError) as cm:
            locate.find_project(None, cwd="/")
        self.assertIn("git was not found", str(cm.exception))

    def test_a_symlink_to_the_repo_gives_the_real_path(self):
        # Two names for one repo have to give one answer. This is the rule
        # behind the resolve() in setUp: the canonical path is the answer, not
        # whatever name the caller happened to use.
        link = self.root / "link-to-demo"
        link.symlink_to(self.repo)
        self.assertEqual(locate.find_project(str(link), cwd="/"), self.repo)


class BranchTest(LocateTestCase):
    def test_current_branch(self):
        self.assertEqual(locate.current_branch(self.repo), "main")

    def test_branches_come_back_newest_first(self):
        names = [b.name for b in locate.list_branches(self.repo)]
        self.assertEqual(names, ["side", "main"])

    def test_the_checked_out_branch_is_marked(self):
        found = {b.name: b.has_worktree for b in locate.list_branches(self.repo)}
        self.assertTrue(found["main"])
        self.assertFalse(found["side"])


class ListMarkdownTest(LocateTestCase):
    def test_ignored_files_never_show_up(self):
        found = locate.list_markdown(self.repo, "main")
        self.assertNotIn("node_modules/junk.md", found)

    def test_a_branch_only_file_is_listed_on_that_branch(self):
        self.assertIn("docs/two.md", locate.list_markdown(self.repo, "side"))
        self.assertNotIn("docs/two.md", locate.list_markdown(self.repo, "main"))

    def test_recently_touched_files_come_first(self):
        (self.repo / "docs" / "one.md").write_text("# one, touched again\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "third", when="2026-09-03T10:00:00")
        self.assertEqual(locate.list_markdown(self.repo, "main")[0], "docs/one.md")

    def test_the_rest_come_back_in_alphabetical_order(self):
        # The fixture has too few commits to ever reach the sorted() half:
        # `git log -50` covers every commit it has, so every file counts as
        # recent. Dropping RECENT_COMMITS to 1 is the only way to exercise it.
        original = locate.RECENT_COMMITS
        locate.RECENT_COMMITS = 1
        self.addCleanup(setattr, locate, "RECENT_COMMITS", original)

        for name in ("zeta.md", "alpha.md"):
            (self.repo / name).write_text(f"# {name}\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "more", when="2026-09-03T10:00:00")
        (self.repo / "docs" / "one.md").write_text("# touched again\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "newest", when="2026-09-04T10:00:00")

        found = locate.list_markdown(self.repo, "main")
        self.assertEqual(found[0], "docs/one.md")
        self.assertEqual(found[1:], ["README.md", "alpha.md", "zeta.md"])

    def test_only_markdown_is_listed(self):
        found = locate.list_markdown(self.repo, "main")
        self.assertTrue(all(p.endswith(".md") for p in found))


class ReadFileTest(LocateTestCase):
    def test_the_working_copy_wins_on_the_current_branch(self):
        # This is the rule that breaks quietly. An edit on disk is not in git
        # yet, so reading from git would play the old version of the file.
        (self.repo / "docs" / "one.md").write_text("# not committed yet\n")
        self.assertIn("not committed yet",
                      locate.read_file(self.repo, "main", "docs/one.md"))

    def test_another_branch_comes_from_git_not_from_disk(self):
        (self.repo / "docs" / "one.md").write_text("# not committed yet\n")
        self.assertIn("one on main",
                      locate.read_file(self.repo, "side", "docs/one.md"))

    def test_a_missing_file_says_so(self):
        with self.assertRaises(locate.LocateError):
            locate.read_file(self.repo, "main", "docs/nope.md")

    def test_a_bad_branch_keeps_its_own_message(self):
        # "that file is not on this branch" and "that branch does not exist"
        # are different problems. Measured: git answers "invalid object name"
        # for the second, which carries none of the missing-path wording.
        with self.assertRaises(locate.LocateError) as cm:
            locate.read_file(self.repo, "nosuchbranch", "docs/one.md")
        self.assertNotIn("is not on branch", str(cm.exception))
        self.assertIn("invalid object name", str(cm.exception).lower())

    def test_the_current_branch_falls_back_to_git_when_the_file_is_gone(self):
        # Disk wins only when the file is actually there. Deleted on disk but
        # still committed has to come back from git.
        (self.repo / "docs" / "one.md").unlink()
        self.assertIn("one on main",
                      locate.read_file(self.repo, "main", "docs/one.md"))

    def test_read_path_reads_a_plain_file(self):
        target = self.root / "loose.md"
        target.write_text("# loose\n")
        self.assertEqual(locate.read_path(str(target)), "# loose\n")

    def test_read_path_on_a_missing_file_says_so(self):
        with self.assertRaises(locate.LocateError):
            locate.read_path(str(self.root / "nope.md"))


if __name__ == "__main__":
    unittest.main()
