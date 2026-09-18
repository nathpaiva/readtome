"""Tests for readtome/speak.py.

A fake `say` goes on the PATH. It writes its arguments to a file and exits, so
a test run is silent and fast.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pty
import re
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parse   # noqa: E402
import speak   # noqa: E402


class FakeSayTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = self.root / "say.log"
        # The voices this machine pretends to have. A test can rewrite it.
        self.voices = self.root / "voices.txt"
        self.set_voices("Samantha", "Samantha (Enhanced)", "Luciana")

        fake = self.root / "say"
        fake.write_text(
            "#!/bin/sh\n"
            # `say -v ?` lists the voices, and readtome reads that list to
            # decide which one it can use. A fake that ignored it would make
            # every voice test prove nothing.
            f'if [ "$1" = "-v" ] && [ "$2" = "?" ]; then cat "{self.voices}"; exit 0; fi\n'
            f'printf "%s\\n" "$*" >> "{self.log}"\n'
            # Real `say` takes seconds. Exiting at once would end the key loop
            # before it polls, and every key test would race.
            "sleep 0.2\n"
        )
        fake.chmod(0o755)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.root}{os.pathsep}{self.old_path}"
        # The first spawn in a fresh process pays a cold start. Measured on a
        # Mac: 300ms cold against 8ms warm. No sensible key timeout covers
        # that, so the kill would win the race and the fake would never write
        # its first line. One throwaway call pays the cost once.
        subprocess.run(["say", "warm up"], capture_output=True)
        self.log.unlink(missing_ok=True)

    def set_voices(self, *names):
        """Write the voice list in the shape `say -v ?` prints it."""
        locale = {"Luciana": "pt_BR"}
        self.voices.write_text("".join(
            f"{name:<19} {locale.get(name, 'en_US')}    # Hello.\n"
            for name in names))
        # The lookup is cached for the run, so a new list needs a clean slate.
        speak.installed_voices.cache_clear()
        speak.best_voice.cache_clear()

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        speak.installed_voices.cache_clear()
        speak.best_voice.cache_clear()
        self.tmp.cleanup()

    def calls(self):
        if not self.log.exists():
            return []
        return [line for line in self.log.read_text().splitlines() if line]

    def spoken(self):
        """Just the text of each call, with the flags cut off.

        Cut at the rate, not at a count of spaces. A voice name can hold a
        space, and `Samantha (Enhanced)` does.
        """
        return [re.sub(r"^.*?-r \d+ -- ", "", line) for line in self.calls()]


BLOCKS = [
    parse.Block(kind="heading", line=5, lang="en", sentences=["Data flow"], level=2),
    parse.Block(kind="prose", line=7, lang="en", sentences=["First one.", "Second one."]),
    parse.Block(kind="prose", line=9, lang="pt", sentences=["Terceira frase."]),
]


class VoiceFromTheEnvironmentTest(FakeSayTestCase):
    """Pinning a voice without touching the code."""

    def pin(self, lang, name):
        variable = speak.voice_variable(lang)
        old = os.environ.get(variable)
        os.environ[variable] = name
        self.addCleanup(lambda: os.environ.__setitem__(variable, old)
                        if old is not None else os.environ.pop(variable, None))
        speak.best_voice.cache_clear()

    def test_the_variable_wins_over_the_built_in_choice(self):
        self.set_voices("Samantha", "Samantha (Enhanced)", "Albert")
        self.pin("en", "Albert")
        self.assertEqual(speak.best_voice("en"), "Albert")

    def test_each_language_has_its_own_variable(self):
        self.set_voices("Samantha", "Luciana", "Albert")
        self.pin("pt", "Albert")
        self.assertEqual(speak.best_voice("pt"), "Albert")
        self.assertEqual(speak.best_voice("en"), "Samantha",
                         "pinning one language must not move the other")

    def test_the_language_switch_still_works_when_a_voice_is_pinned(self):
        # This is the whole point. `--voice` pins one voice and turns
        # detection off; the variable pins one voice PER language, so a
        # document that mixes the two still changes voice.
        self.set_voices("Samantha", "Luciana", "Albert")
        self.pin("en", "Albert")
        speak.play(BLOCKS, interactive=False, stream=io.StringIO())
        self.assertIn("Albert", self.calls()[0])
        self.assertIn("Luciana", self.calls()[3])

    def test_a_voice_that_is_not_installed_says_so(self):
        # `say` takes an unknown voice, exits 0, and reads everything in the
        # default voice. Without this check a typo costs a whole document in
        # the wrong voice, with nothing on screen to explain it.
        self.set_voices("Samantha", "Luciana")
        self.pin("en", "Ava")
        with self.assertRaises(RuntimeError) as caught:
            speak.best_voice("en")
        message = str(caught.exception)
        self.assertIn("READTOME_VOICE_EN", message)
        self.assertIn("Ava", message)
        self.assertIn("say -v", message, "the message must say how to look")

    def test_an_empty_variable_is_the_same_as_not_setting_it(self):
        # An `export READTOME_VOICE_EN=` left over in a shell config must not
        # turn into an error about a voice called nothing.
        self.set_voices("Samantha", "Samantha (Enhanced)")
        self.pin("en", "   ")
        self.assertEqual(speak.best_voice("en"), "Samantha (Enhanced)")

    def test_a_forced_voice_still_beats_the_variable(self):
        self.set_voices("Samantha", "Albert")
        self.pin("en", "Albert")
        block = parse.Block(kind="prose", line=1, lang="en", sentences=["Hi."])
        self.assertEqual(speak.voice_for(block, "Samantha"), "Samantha")


class BestVoiceTest(FakeSayTestCase):
    """Which voice readtome asks for, and what happens when it is missing."""

    def test_the_premium_voice_wins_over_the_enhanced_one(self):
        # The order in VOICES is best first, so a machine with both takes the
        # better one without anyone choosing.
        self.set_voices("Samantha", "Samantha (Enhanced)", "Ava (Premium)")
        self.assertEqual(speak.best_voice("en"), "Ava (Premium)")

    def test_the_enhanced_voice_wins_when_it_is_installed(self):
        self.assertEqual(speak.best_voice("en"), "Samantha (Enhanced)")

    def test_the_plain_voice_is_used_when_the_enhanced_one_is_missing(self):
        # Enhanced voices are a download. Someone cloning this repo has the
        # plain one and must not meet an error about a voice they never saw.
        self.set_voices("Samantha", "Luciana")
        self.assertEqual(speak.best_voice("en"), "Samantha")

    def test_a_name_filling_its_whole_column_is_still_read(self):
        # `Samantha (Enhanced)` is exactly as wide as the name column, so only
        # one space separates it from the locale. Splitting on runs of spaces
        # loses it, and the enhanced voice would look uninstalled on a machine
        # that has it.
        self.assertIn("Samantha (Enhanced)", speak.installed_voices())

    def test_an_unknown_language_falls_back_to_english(self):
        self.assertEqual(speak.best_voice("de"), speak.best_voice("en"))

    def test_with_nothing_installed_it_asks_for_the_plain_name(self):
        # `say` then fails naming the voice it could not find, which is a
        # better error than us quietly picking a stranger's voice.
        self.set_voices()
        self.assertEqual(speak.best_voice("en"), "Samantha")

    def test_a_forced_voice_never_reads_the_list(self):
        self.set_voices()      # an empty list would break any lookup
        block = parse.Block(kind="prose", line=1, lang="en", sentences=["Hi."])
        self.assertEqual(speak.voice_for(block, "Albert"), "Albert")


class SentenceStartingWithADashTest(FakeSayTestCase):
    """A sentence that looks like a flag. Real `say`, not the fake one.

    The fake would happily accept anything, so it cannot fail the way `say`
    does. This one calls `say -o` for real and writes to a file, so it makes
    no sound and still proves the command line is right.
    """

    def test_real_say_accepts_a_sentence_that_starts_with_two_dashes(self):
        os.environ["PATH"] = self.old_path      # step over the fake
        out = self.root / "out.aiff"
        done = subprocess.run(
            ["say", "-r", "300", "-o", str(out), "--",
             "--voice overrides all of it, and it is only text here."],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(out.exists())

    def test_the_dash_dash_is_in_the_command(self):
        speak.start_say("--from is a flag.", "Samantha", 220).wait()
        self.assertIn("--", self.calls()[0].split())


class HowLongTest(unittest.TestCase):
    """The words, turned into a length a person can plan around."""

    def blocks(self, *counts):
        """One block per number, holding that many one word sentences."""
        return [parse.Block(kind="prose", line=1, lang="en",
                            sentences=["word"] * n) for n in counts]

    def test_it_counts_every_sentence_of_every_block(self):
        seconds = speak.reading_seconds(self.blocks(3, 4), 0, rate=60)
        # 7 words at 60 a minute is 7 seconds, plus the correction.
        self.assertAlmostEqual(seconds, 7 * speak.SLOWER_THAN_IT_SAYS, places=6)

    def test_starting_later_counts_less(self):
        both = speak.reading_seconds(self.blocks(3, 4), 0, rate=60)
        second = speak.reading_seconds(self.blocks(3, 4), 1, rate=60)
        self.assertLess(second, both)
        self.assertAlmostEqual(second, 4 * speak.SLOWER_THAN_IT_SAYS, places=6)

    def test_a_faster_rate_takes_less_time(self):
        slow = speak.reading_seconds(self.blocks(100), 0, rate=180)
        fast = speak.reading_seconds(self.blocks(100), 0, rate=360)
        self.assertAlmostEqual(slow / fast, 2.0, places=6)

    def test_the_announcement_of_a_code_block_is_counted(self):
        # A code block is not read out, but its announcement is spoken, and
        # a document full of them would be badly underestimated without it.
        blocks = parse.parse("```python\nx = 1\n```\n")
        self.assertGreater(speak.reading_seconds(blocks, 0, rate=220), 0)

    def test_it_reads_as_a_person_would_say_it(self):
        self.assertEqual(speak.how_long(0), "less than a minute")
        self.assertEqual(speak.how_long(20), "less than a minute")
        self.assertEqual(speak.how_long(60), "about 1 min")
        self.assertEqual(speak.how_long(14 * 60), "about 14 min")
        self.assertEqual(speak.how_long(59.4 * 60), "about 59 min")
        self.assertEqual(speak.how_long(60 * 60), "about 1 h 00 min")
        self.assertEqual(speak.how_long(95 * 60), "about 1 h 35 min")

    def test_it_never_says_about_zero_minutes(self):
        # Rounding a 20 second read down would print "about 0 min", which
        # reads as a bug.
        for seconds in range(0, 60, 5):
            self.assertNotIn("0 min", speak.how_long(seconds))


class EstimateMatchesRealSpeechTest(unittest.TestCase):
    """The estimate against `say` itself, measured once and written down.

    Synthesising these takes about four minutes, far too slow for every run,
    so the measurements live here as numbers. They came from one real
    document, 845 words in 82 sentences, timed with `say -o` and `afinfo`.
    This does not re-measure `say`. It fails when the formula drifts away
    from what `say` was doing when it was last measured.
    """

    WORDS = 845
    MEASURED = {180: 282.0, 220: 245.0, 300: 186.0}   # seconds

    def test_every_rate_lands_within_a_tenth_of_the_real_thing(self):
        blocks = [parse.Block(kind="prose", line=1, lang="en",
                              sentences=["word"] * self.WORDS)]
        for rate, real in self.MEASURED.items():
            with self.subTest(rate=rate):
                guess = speak.reading_seconds(blocks, 0, rate)
                off = abs(guess - real) / real
                self.assertLess(off, 0.10,
                                f"r{rate}: guessed {guess:.0f}s against a "
                                f"measured {real:.0f}s, {off:.0%} out")


class PlayTest(FakeSayTestCase):
    def test_one_say_call_per_sentence(self):
        speak.play(BLOCKS, interactive=False)
        self.assertEqual(len(self.calls()), 4)

    def test_the_rate_is_passed_through(self):
        speak.play(BLOCKS, rate=260, interactive=False)
        self.assertTrue(all("-r 260" in c for c in self.calls()))

    def test_the_voice_follows_the_block_language(self):
        speak.play(BLOCKS, interactive=False)
        self.assertIn(speak.best_voice("en"), self.calls()[0])
        self.assertIn(speak.best_voice("pt"), self.calls()[3])

    def test_a_forced_voice_wins_over_detection(self):
        speak.play(BLOCKS, voice="Albert", interactive=False)
        self.assertTrue(all("Albert" in c for c in self.calls()))

    def test_start_skips_the_blocks_before_it(self):
        speak.play(BLOCKS, start=2, interactive=False)
        self.assertEqual(len(self.calls()), 1)

    def test_it_returns_the_last_block_it_started(self):
        self.assertEqual(speak.play(BLOCKS, interactive=False), 2)


class DescribeTest(unittest.TestCase):
    def test_a_heading_shows_its_level_and_line(self):
        line = speak.describe(BLOCKS[0])
        self.assertIn("##", line)
        self.assertIn("5", line)
        self.assertIn("Data flow", line)

    def test_prose_prints_nothing(self):
        self.assertIsNone(speak.describe(BLOCKS[1]))

    def test_a_code_block_prints_its_line(self):
        block = parse.Block(kind="code", line=28, sentences=["code block, typescript"])
        self.assertIn("28", speak.describe(block))


class FakeKeys:
    """Hands out one key per call, then None for ever.

    It sleeps the timeout it is given, the way the real reader does. Without
    that wait the key lands microseconds after `say` starts, the kill wins the
    race, and the fake never reaches its own first line. Measured on a Mac: a
    warm spawn writes after about 8ms, 16ms at worst, so the 50ms poll timeout
    leaves room to spare.
    """

    def __init__(self, *keys):
        self.pending = list(keys)

    def __call__(self, timeout):
        time.sleep(timeout)
        return self.pending.pop(0) if self.pending else None


class KeyTest(FakeSayTestCase):
    def test_space_pauses_then_replays_the_same_sentence(self):
        one = [parse.Block(kind="prose", line=1, sentences=["Only one here."])]
        speak.play(one, interactive=True, keys=FakeKeys(" ", " "))
        # played, paused, played again: the same sentence twice
        voice = speak.best_voice("en")
        self.assertEqual(self.calls(),
                         [f"-v {voice} -r 220 -- Only one here."] * 2)

    def test_q_stops_before_the_rest(self):
        speak.play(BLOCKS, interactive=True, keys=FakeKeys("q"))
        self.assertEqual(len(self.calls()), 1)

    def test_n_jumps_to_the_next_heading(self):
        blocks = [
            parse.Block(kind="heading", line=1, sentences=["One"], level=1),
            parse.Block(kind="prose", line=2, sentences=["Skip this one."]),
            parse.Block(kind="heading", line=3, sentences=["Two"], level=1),
        ]
        speak.play(blocks, interactive=True, keys=FakeKeys(None, "n"))
        spoken = " ".join(self.calls())
        self.assertIn("One", spoken)
        self.assertIn("Two", spoken)
        self.assertNotIn("Skip this one", spoken)

    def test_plus_raises_the_rate_and_replays(self):
        one = [parse.Block(kind="prose", line=1, sentences=["Only one here."])]
        speak.play(one, rate=220, interactive=True, keys=FakeKeys("+"))
        self.assertIn("-r 240", self.calls()[-1])

    def test_the_rate_stops_at_the_ceiling(self):
        one = [parse.Block(kind="prose", line=1, sentences=["Only one here."])]
        speak.play(one, rate=speak.RATE_MAX, interactive=True, keys=FakeKeys("+"))
        self.assertIn(f"-r {speak.RATE_MAX}", self.calls()[-1])

    def test_an_unknown_key_is_ignored(self):
        one = [parse.Block(kind="prose", line=1, sentences=["Only one here."])]
        speak.play(one, interactive=True, keys=FakeKeys("z"))
        self.assertEqual(len(self.calls()), 1)


    def test_a_pause_resumes_when_the_test_keys_run_dry(self):
        # A test reader hands over a fixed list. When it empties during a
        # pause the loop has to give up waiting, or the suite hangs. This is
        # the branch _waiting_forever exists for.
        one = [parse.Block(kind="prose", line=1, sentences=["Only one here."])]
        speak.play(one, interactive=True, keys=FakeKeys(" "))
        self.assertEqual(len(self.calls()), 2)

    def test_q_while_paused_quits(self):
        one = [parse.Block(kind="prose", line=1, sentences=["Only one here."])]
        speak.play(one, interactive=True, keys=FakeKeys(" ", "q"))
        self.assertEqual(len(self.calls()), 1)

    def test_minus_lowers_the_rate_and_replays(self):
        one = [parse.Block(kind="prose", line=1, sentences=["Only one here."])]
        speak.play(one, rate=220, interactive=True, keys=FakeKeys("-"))
        self.assertIn("-r 200", self.calls()[-1])

    def test_the_rate_stops_at_the_floor(self):
        one = [parse.Block(kind="prose", line=1, sentences=["Only one here."])]
        speak.play(one, rate=speak.RATE_MIN, interactive=True, keys=FakeKeys("-"))
        self.assertIn(f"-r {speak.RATE_MIN}", self.calls()[-1])

    def test_b_jumps_back_to_the_previous_heading(self):
        # start=1 on purpose. A sentence lasts about 200ms and a key poll
        # takes 50ms, so starting at the top would fire "b" while still on the
        # first block, where there is nothing to go back to.
        blocks = [
            parse.Block(kind="heading", line=1, sentences=["One"], level=1),
            parse.Block(kind="heading", line=3, sentences=["Two"], level=1),
        ]
        speak.play(blocks, start=1, interactive=True, keys=FakeKeys("b", "q"))
        self.assertEqual(self.spoken(), ["Two", "One"])


class NoRealSayTest(unittest.TestCase):
    """Nothing in this suite may reach the real macOS `say`.

    A test that escapes the fake makes the machine speak on every run. It
    does not fail, and no assertion sees it, so the only thing that catches
    it is a person in the room. This walks the test files and checks that
    every class reaching speak.play sits on a fixture that puts a fake `say`
    on the PATH.

    It searches the text, not the call graph, so a class that only names
    speak.play in a comment counts as reaching it. That is the safe way round:
    a false alarm costs a moment, a miss costs a machine that talks to itself.
    This class is the one exception, because it has to name the thing it looks
    for.
    """

    FAKES = {"FakeSayTestCase", "CliTestCase"}

    def test_every_class_that_speaks_uses_the_fake(self):
        import ast

        folder = Path(__file__).resolve().parent
        for path in sorted(folder.glob("test_*.py")):
            source = path.read_text()
            for node in ast.parse(source).body:
                if not isinstance(node, ast.ClassDef) or node.name in self.FAKES:
                    continue
                if node.name == type(self).__name__:
                    continue      # this class names speak.play in its own text
                body = ast.get_source_segment(source, node) or ""
                if "speak.play" not in body:
                    continue
                bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
                self.assertTrue(
                    self.FAKES.intersection(bases),
                    f"{path.name}:{node.name} reaches speak.play with no fake say")


class WaitingForeverTest(unittest.TestCase):
    def test_it_tells_the_two_readers_apart(self):
        # The whole point: a real pause waits with no time limit, a test
        # reader that has run dry must not leave the loop spinning. Pin it to
        # a bare assertion, so forcing it to either answer fails here first.
        self.assertTrue(speak._waiting_forever(speak.KeyReader(fd=0)))
        self.assertFalse(speak._waiting_forever(FakeKeys()))


class StatusLineTest(unittest.TestCase):
    def test_it_shows_the_line_the_count_and_the_rate(self):
        line = speak.status_line(BLOCKS[1], index=0, total=2, rate=260, paused=False)
        self.assertIn("7", line)
        self.assertIn("1/2", line)
        self.assertIn("260", line)

    def test_paused_is_visible(self):
        line = speak.status_line(BLOCKS[1], index=0, total=2, rate=220, paused=True)
        self.assertIn("⏸", line)


class TerminalRestoreTest(unittest.TestCase):
    def test_the_terminal_goes_back_after_a_crash(self):
        import pty
        import termios

        def flags(fd):
            # A pty nobody has read carries the PENDIN bit, which the kernel
            # sets on its own. Compare without it, or this measures a kernel
            # artefact instead of what cbreak did.
            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~termios.PENDIN
            return attrs

        leader, follower = pty.openpty()
        self.addCleanup(os.close, leader)
        self.addCleanup(os.close, follower)
        before = flags(follower)

        stream = os.fdopen(follower, "w", closefd=False)
        with self.assertRaises(ValueError):
            with speak.cbreak(stream):
                raise ValueError("boom")

        self.assertEqual(flags(follower), before)


REPO = str(Path(__file__).resolve().parents[1])

# The child runs the real `play` with no injected reader, so it detects the
# terminal itself and reads keys through `KeyReader`.
CHILD = """
import json, sys
sys.path.insert(0, {repo!r})
import parse, speak
blocks = parse.parse(open({doc!r}).read())
progress = {{}}
speak.play(blocks, rate=220, progress=progress)
open({out!r}, "w").write(json.dumps(progress))
"""

UP = "\x1b[A"
DOWN = "\x1b[B"

DOC = """# One

One here. Two here. Three here. Four here. Five here. Six here.
Seven here. Eight here. Nine here. Ten here. Eleven here. Twelve here.

# Two

The second part starts here.
"""


def playing(rate: int) -> str:
    """The bit of the status line that says the rate, and only that line.

    Three spaces, on purpose. The estimate printed before the reading starts
    ends in "at r220" with one space, so waiting for a bare "r220" matches it
    and the test presses its key before a single word has been spoken.
    """
    return f"   r{rate}"


class RealTerminalTest(FakeSayTestCase):
    """The keys, pressed on a pty instead of handed over as a list.

    Every other key test injects a reader, so it proves the decision and not
    the device. These write bytes into a terminal and read the screen back.
    """

    def drive(self, steps, timeout=25.0):
        """Wait for each line on screen, then type its key. Returns progress.

        `steps` is a list of (text to wait for, key to press). Waiting on the
        screen instead of sleeping keeps the test off the clock.
        """
        doc = self.root / "doc.md"
        doc.write_text(DOC)
        out = self.root / "progress.json"
        source = CHILD.format(repo=REPO, doc=str(doc), out=str(out))

        leader, follower = pty.openpty()
        proc = subprocess.Popen([sys.executable, "-c", source], stdin=follower,
                                stdout=follower, stderr=follower)
        os.close(follower)
        self.addCleanup(proc.kill)
        self.addCleanup(os.close, leader)

        seen = ""
        self.screen = ""      # the whole transcript, never cut
        deadline = time.time() + timeout
        for expect, key in steps:
            while expect not in seen:
                self.assertLess(time.time(), deadline,
                                f"never saw {expect!r} on screen. Got: {seen!r}")
                ready, _, _ = select.select([leader], [], [], 0.2)
                if not ready:
                    continue
                try:
                    data = os.read(leader, 4096)
                except OSError:   # the child closed the pty
                    break
                if not data:
                    break
                text = data.decode("utf-8", "replace")
                seen += text
                self.screen += text
            self.assertIn(expect, seen, f"never saw {expect!r} on screen")
            # Only look forward, so the next wait cannot match an old line.
            seen = seen.split(expect, 1)[1]
            os.write(leader, key.encode())

        # Keep reading to the end. A pty whose buffer fills blocks the child
        # on its next write, and the exit we are waiting for never comes.
        while proc.poll() is None:
            self.assertLess(time.time(), deadline, "the child never exited")
            ready, _, _ = select.select([leader], [], [], 0.2)
            if not ready:
                continue
            try:
                os.read(leader, 4096)
            except OSError:
                break
        proc.wait(timeout=5)
        return json.loads(out.read_text())

    def test_the_shifted_twins_change_the_speed_too(self):
        # `+` is shift and `=` on one key, `_` is shift and `-` on another.
        # Taking only one of each pair means holding shift breaks the speed.
        for key, expected in (("=", 240), ("_", 200)):
            with self.subTest(key=key):
                progress = self.drive([(playing(220), key), (playing(expected), "q")])
                self.assertEqual(progress["rate"], expected)

    def test_minus_lowers_the_rate_from_a_real_terminal(self):
        progress = self.drive([(playing(220), "-"), (playing(200), "q")])
        self.assertEqual(progress["rate"], 200)

    def test_plus_raises_the_rate_from_a_real_terminal(self):
        progress = self.drive([(playing(220), "+"), (playing(240), "q")])
        self.assertEqual(progress["rate"], 240)

    def test_space_pauses_and_plays_again_from_a_real_terminal(self):
        self.drive([(playing(220), " "), ("\u23f8", " "), ("\u25b6", "q")])

    def test_the_down_arrow_moves_one_sentence_on(self):
        # The prose block holds 6 sentences, so this stays inside one block.
        self.drive([("sentence 1/6", DOWN),
                    ("sentence 2/6", DOWN),
                    ("sentence 3/6", "q")])

    def test_the_up_arrow_undoes_the_down_arrow(self):
        self.drive([("sentence 1/6", DOWN + DOWN),
                    ("sentence 3/6", UP),
                    ("sentence 2/6", "q")])

    def test_the_down_arrow_crosses_into_the_next_block(self):
        # The heading is one sentence long, so down leaves it at once.
        self.drive([("sentence 1/1   r220", DOWN),
                    ("sentence 1/6", "q")])

    def test_the_up_arrow_lands_on_the_last_sentence_of_the_block_before(self):
        # Sentence 6 of 6, not 1 of 6. Landing on the first would make up and
        # down disagree, and a document would never come back the same way.
        #
        # Watching the screen is not enough here: landing on 1 of 6 reaches
        # 6 of 6 on its own a few seconds later, and the test would pass for
        # the wrong reason. What `say` was actually handed is the proof.
        self.drive([("sentence 1/6", DOWN * 6),
                    ("# Two", UP),
                    ("sentence 6/6", "q")])
        # Which sentence came FIRST after the jump. Landing on 1 of 6 also
        # reaches 6 of 6 a second later, on its own, so only the order proves
        # anything. The `say` log cannot: six arrows arrive at once and each
        # process is killed before it manages to write its line.
        after = self.screen.split("# Two", 1)[1]
        landed = re.search(r"sentence (\d)/6", after).group(1)
        self.assertEqual(landed, "6",
                         f"up landed on sentence {landed} of 6, not the last")

    def test_the_up_arrow_at_the_very_start_stays_put(self):
        # One press too many must not drop out of the reading.
        progress = self.drive([("sentence 1/1   r220", UP + UP),
                               ("sentence 1/1   r220", "q")])
        self.assertEqual(progress["block"], 0)

    def test_next_and_back_move_between_headings_from_a_real_terminal(self):
        self.drive([(playing(220), "n"), ("# Two", "b"), ("# One", "q")])
        # Reaching the second heading proves nothing on its own: the reader
        # gets there anyway once it runs out of sentences. The jump is what
        # skipped them, so the last one must never have been spoken.
        self.assertFalse([c for c in self.calls() if "Twelve here." in c],
                         "the reader never jumped, it just read to the end")


class NotATerminalTest(FakeSayTestCase):
    """The silent fallback that hid the `-` key bug for a whole session."""

    def test_it_warns_when_it_works_out_that_this_is_not_a_terminal(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            speak.play(BLOCKS[:1], stream=io.StringIO())
        self.assertIn("not a terminal", error.getvalue())
        self.assertIn("Ctrl+C", error.getvalue())

    def test_it_stays_quiet_when_the_caller_picked_the_mode(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            speak.play(BLOCKS[:1], interactive=False, stream=io.StringIO())
        self.assertEqual(error.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
