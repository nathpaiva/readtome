"""Say the blocks out loud, one sentence at a time.

One `say` process per sentence is the whole trick. `say` hands the text to the
system speech daemon and steps aside, so stopping the process does not pause
the audio, but killing it does stop it. Short sentences mean the loop reaches
a decision point every few seconds.
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import tty

from parse import VOICES, Block


def check_say() -> None:
    """Fail early and clearly when this is not a Mac."""
    if shutil.which("say") is None:
        raise RuntimeError("`say` was not found. readtome needs macOS.")


def describe(block: Block) -> str | None:
    """The line printed for a block, or None when it prints nothing.

    Only headings, code and tables get a line. Printing one per paragraph
    would bury the outline in noise.
    """
    text = block.sentences[0] if block.sentences else ""
    if block.kind == "heading":
        return f"→ {block.line:>4}  {'#' * block.level} {text}"
    if block.kind in ("code", "table"):
        return f"  {block.line:>4}  [{block.kind}] {text[:60]}"
    return None


# `en_US`, `pt_BR`, `ar_001`, `en` on its own. Every line of `say -v ?` ends
# its first half with one of these.
LOCALE = re.compile(r"[a-z]{2}([-_][A-Za-z0-9]+)?")


@functools.lru_cache(maxsize=1)
def installed_voices() -> frozenset[str]:
    """Every voice name `say` knows on this machine.

    Read once per run. It costs about 400ms, and only when no `--voice` was
    given, since a forced voice never asks.
    """
    done = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    names = set()
    for line in done.stdout.splitlines():
        # `Samantha (Enhanced) en_US    # Hello! My name is Samantha.`
        # A long name fills its whole column, leaving one space before the
        # locale, so splitting on runs of spaces loses exactly the enhanced
        # voices. Cut the example off first, then take the locale off the end.
        left = line.split("#", 1)[0].rstrip()
        parts = left.rsplit(None, 1)
        if len(parts) == 2 and LOCALE.fullmatch(parts[1]):
            names.add(parts[0].strip())
    return frozenset(names)


@functools.lru_cache(maxsize=None)
def best_voice(lang: str) -> str:
    """The first voice we want for this language that is installed here."""
    wanted = VOICES.get(lang, VOICES["en"])
    here = installed_voices()
    for name in wanted:
        if name in here:
            return name
    # None of them is here. Hand `say` the plain name, so its own error names
    # the voice it could not find instead of us quietly picking a stranger.
    return wanted[-1]


def voice_for(block: Block, forced: str | None) -> str:
    return forced or best_voice(block.lang)


RATE_STEP = 20
RATE_MIN = 80
RATE_MAX = 720

CLEAR_LINE = "\r\033[K"

NOT_A_TERMINAL = (
    "readtome: this is not a terminal, so the keys are off.\n"
    "You cannot pause it, change the speed, or press q to stop. "
    "Ctrl+C stops it."
)

_RUNNING: list[subprocess.Popen] = []


def start_say(sentence: str, voice: str, rate: int) -> subprocess.Popen:
    # `--` closes the options. Without it a sentence starting with a dash,
    # which any document about flags has, is read as an option: `say` prints
    # "unrecognized option", exits, and the sentence is silently skipped.
    proc = subprocess.Popen(["say", "-v", voice, "-r", str(rate), "--",
                             sentence])
    _RUNNING[:] = [p for p in _RUNNING if p.poll() is None]
    _RUNNING.append(proc)
    return proc


@atexit.register
def _stop_everything() -> None:
    """Quitting must not leave a voice talking to an empty room."""
    for proc in _RUNNING:
        if proc.poll() is None:
            proc.kill()


ESCAPE = b"\x1b"

# What the arrow keys send. Three bytes, not one.
ARROWS = {b"A": "up", b"B": "down", b"C": "right", b"D": "left"}


class KeyReader:
    """One key press, read straight from the terminal file descriptor.

    Not `sys.stdin.read(1)`. That decodes a whole chunk into a Python buffer,
    so the `[A` of an arrow key would sit there unseen while `select` reported
    the terminal as empty, and every arrow would arrive as a bare escape.
    """

    def __init__(self, fd: int | None = None):
        self.fd = sys.stdin.fileno() if fd is None else fd
        self.pending = b""

    def _fill(self, timeout: float | None) -> bool:
        if self.pending:
            return True
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return False
        chunk = os.read(self.fd, 64)
        if not chunk:
            return False
        self.pending += chunk
        return True

    def _take(self, count: int) -> bytes:
        taken, self.pending = self.pending[:count], self.pending[count:]
        return taken

    def __call__(self, timeout: float | None = None) -> str | None:
        """A key, or None when the timeout runs out. Arrows come back named."""
        if not self._fill(timeout):
            return None
        if not self.pending.startswith(ESCAPE):
            return self._take(_utf8_length(self.pending[0])).decode(
                "utf-8", "replace")

        self._take(1)
        # A bare escape arrives alone. An arrow sends its `[A` in the same
        # breath, so a short wait tells the two apart.
        if not self._fill(0.05) or not self.pending.startswith(b"["):
            return "escape"
        self._take(1)
        if not self._fill(0.05):
            return "escape"
        return ARROWS.get(self._take(1), "")


def _utf8_length(first: int) -> int:
    """How many bytes the character starting with this one takes."""
    if first < 0x80:
        return 1
    if first < 0xE0:
        return 2
    if first < 0xF0:
        return 3
    return 4


@contextlib.contextmanager
def cbreak(stream):
    """Single key reads, with output processing and Ctrl+C left alone.

    The `finally` is the point. Without it, an error in the middle leaves a
    shell that does not echo what the user types.
    """
    try:
        fd = stream.fileno()
        saved = termios.tcgetattr(fd)
    except (AttributeError, ValueError, termios.error):
        # Only these three mean "this is not a terminal". A bare OSError would
        # also swallow a permission problem or a torn down pty, and the tool
        # would carry on with cbreak silently not applied. Checked: a stream
        # with no fileno raises ValueError, and a file that is not a tty raises
        # termios.error, which is NOT an OSError subclass.
        yield
        return
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


# `say -r N` does not deliver N words a minute. Measured on a real document
# with `say -o` and `afinfo`, the reading takes about 7% longer than words
# divided by rate. The gap drifts a little with the rate: about 0% at r180,
# 6% at r220 and 10% at r300, so one constant near the middle keeps every
# rate inside 7%.
SLOWER_THAN_IT_SAYS = 1.07


def reading_seconds(blocks: list[Block], start: int, rate: int) -> float:
    """Roughly how long the blocks from `start` take to read out loud."""
    words = sum(len(sentence.split())
                for block in blocks[start:]
                for sentence in block.sentences)
    return words / rate * 60 * SLOWER_THAN_IT_SAYS


def how_long(seconds: float) -> str:
    """The estimate as a person would say it. Always vague on purpose."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return f"about {minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"about {hours} h {rest:02d} min"


def status_line(block: Block, index: int, total: int, rate: int, paused: bool) -> str:
    mark = "⏸" if paused else "▶"
    return f" {mark} {block.line:>4}  sentence {index + 1}/{total}   r{rate}"


def _heading_after(blocks: list[Block], start: int) -> int:
    for i in range(start + 1, len(blocks)):
        if blocks[i].kind == "heading":
            return i
    return len(blocks)


def _heading_before(blocks: list[Block], start: int) -> int:
    for i in range(start - 1, -1, -1):
        if blocks[i].kind == "heading":
            return i
    return 0


def play(blocks: list[Block], rate: int = 220, voice: str | None = None,
         start: int = 0, stream=None, interactive: bool | None = None,
         keys=None, progress: dict | None = None) -> int:
    """Read the blocks out loud. Returns the index of the last one started.

    `stream` defaults to None and is resolved here, not in the signature. A
    default of `sys.stdout` binds at import time, so a test that swaps
    `sys.stdout` later would be ignored, and `play` would read the real
    terminal and wait for a key that never comes.

    `progress`, when given, is filled in with the block index and the rate
    the user ended on. The caller needs this to save where to resume, since
    `+` and `-` change the rate while playing and there is no other way to
    learn the final value.
    """
    stream = stream if stream is not None else sys.stdout
    if interactive is None:
        interactive = stream.isatty() and sys.stdin.isatty()
        if not interactive:
            # Say it out loud. Without this line the tool reads the whole file
            # with no keys and no way to stop, and looks like a broken `-` key.
            print(NOT_A_TERMINAL, file=sys.stderr, flush=True)

    # Before the first word, so the length of what you are starting is known
    # while you can still decide not to start it.
    print(f"{how_long(reading_seconds(blocks, start, rate))} at r{rate}",
          file=stream, flush=True)
    if not interactive:
        index = _play_straight(blocks, rate, voice, start, stream)
        if progress is not None:
            progress.update(block=index, rate=rate)
        return index
    with cbreak(sys.stdin):
        return _play_keys(blocks, rate, voice, start, stream,
                          keys or KeyReader(), progress)


def _play_straight(blocks, rate, voice, start, stream) -> int:
    index = start
    for index in range(start, len(blocks)):
        block = blocks[index]
        line = describe(block)
        if line:
            print(line, file=stream, flush=True)
        for sentence in block.sentences:
            start_say(sentence, voice_for(block, voice), rate).wait()
    return index


def _play_keys(blocks, rate, voice, start, stream, keys, progress=None) -> int:
    index = start
    enter_at = 0      # which sentence the next block opens on
    while 0 <= index < len(blocks):
        block = blocks[index]
        line = describe(block)
        if line:
            stream.write(CLEAR_LINE + line + "\n")
            stream.flush()

        jump = None
        sentence_at = enter_at
        enter_at = 0
        while 0 <= sentence_at < len(block.sentences):
            action, rate = _one_sentence(block, sentence_at, rate, voice,
                                         stream, keys)
            if action == "repeat":
                continue
            if action == "quit":
                stream.write(CLEAR_LINE)
                if progress is not None:
                    progress.update(block=index, rate=rate)
                return index
            if action == "next":
                jump = _heading_after(blocks, index)
                break
            if action == "back":
                jump = _heading_before(blocks, index)
                break
            if action == "rewind":
                sentence_at -= 1
                continue
            sentence_at += 1

        if jump is not None:
            index = jump
        elif sentence_at < 0:
            if index == 0:
                # Nothing before the first sentence of the file. Staying put
                # beats dropping out of the reading on one key too many.
                pass
            else:
                index -= 1
                # The LAST sentence of the block before, so up undoes down.
                enter_at = max(0, len(blocks[index].sentences) - 1)
        else:
            index += 1

    stream.write(CLEAR_LINE)
    index = max(start, len(blocks) - 1)
    if progress is not None:
        progress.update(block=index, rate=rate)
    return index


def _one_sentence(block, at, rate, voice, stream, keys):
    """Play one sentence. Returns (action, rate).

    Actions: done, repeat, next, back, quit.
    """
    total = len(block.sentences)
    proc = start_say(block.sentences[at], voice_for(block, voice), rate)
    stream.write(CLEAR_LINE + status_line(block, at, total, rate, paused=False))
    stream.flush()

    while proc.poll() is None:
        key = keys(0.05)
        if key is None:
            continue
        if key == " ":
            proc.kill()
            proc.wait()
            stream.write(CLEAR_LINE + status_line(block, at, total, rate, paused=True))
            stream.flush()
            while True:
                resume = keys(0.05)
                if resume == " ":
                    return "repeat", rate
                if resume == "q":
                    return "quit", rate
                if resume is None and not _waiting_forever(keys):
                    return "repeat", rate
        if key in ("+", "="):
            proc.kill()
            proc.wait()
            return "repeat", min(RATE_MAX, rate + RATE_STEP)
        if key in ("-", "_"):
            proc.kill()
            proc.wait()
            return "repeat", max(RATE_MIN, rate - RATE_STEP)
        if key == "down":
            proc.kill()
            proc.wait()
            return "done", rate
        if key == "up":
            proc.kill()
            proc.wait()
            return "rewind", rate
        if key == "n":
            proc.kill()
            proc.wait()
            return "next", rate
        if key == "b":
            proc.kill()
            proc.wait()
            return "back", rate
        if key == "q":
            proc.kill()
            proc.wait()
            return "quit", rate

    return "done", rate


def _waiting_forever(keys) -> bool:
    """True for a real terminal reader, false for a test reader run dry.

    A real pause waits for the user with no time limit. A test hands over a
    fixed list of keys, and the loop must not spin once that list is empty.

    This asks what the reader IS, so wrapping one in a counter or a log keeps
    it real. It used to compare identity with a module level function, which
    a wrapper quietly broke.
    """
    return isinstance(keys, KeyReader)
