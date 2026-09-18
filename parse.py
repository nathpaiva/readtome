"""Turn markdown text into blocks that macOS `say` can read out loud.

This module is a pure function. It touches no file, no git and no audio, so
it can be tested on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Block:
    """One piece of the document, ready to be spoken.

    `sentences` is a list and not one string because pause kills the running
    `say` process. The sentence is the unit the player works with.
    """

    kind: str                                    # heading | prose | code | table | list
    line: int                                    # line in the file, 1-based
    lang: str = "en"                             # "pt" or "en"
    sentences: list[str] = field(default_factory=list)
    level: int = 0                               # heading only: 1 for #, 2 for ##


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_QUOTE = re.compile(r"^\s*>\s?")
_MARKS = re.compile(r"[`*~]")


def clean_inline(text: str) -> str:
    """Drop the markdown marks and keep the words.

    The order matters. Images go before links, or `![alt](url)` loses its `!`
    and turns into a plain link.
    """
    text = _HTML_COMMENT.sub("", text)
    text = _IMAGE.sub(lambda m: f"image, {m.group(1)}" if m.group(1) else "image", text)
    text = _LINK.sub(r"\1", text)
    text = _QUOTE.sub("", text)
    text = _MARKS.sub("", text)
    # An underscore is either emphasis or snake_case. A space works for both:
    # dropping it would turn READTOME_ROOTS into one unreadable word.
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


MIN_LEN = 15    # below this, a piece is glued to the next one
MAX_LEN = 200   # above this, a piece is cut again at a comma

_SENT_END = re.compile(r"(?<=[.!?])\s+")
_SOFT_BREAK = re.compile(r"(?<=[,;])\s+")


def split_sentences(text: str) -> list[str]:
    """Cut a paragraph into pieces that are worth one `say` call each.

    MAX_LEN is not about style. Pause kills the sentence that is playing, and
    play starts that same sentence again from the top. A 200 character repeat
    is already annoying; a whole paragraph would be worse.
    """
    pieces = [p.strip() for p in _SENT_END.split(text) if p.strip()]

    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < MIN_LEN:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)

    out: list[str] = []
    for piece in merged:
        out.extend(hard_wrap(piece))
    return out


def hard_wrap(piece: str) -> list[str]:
    """Cut one long piece at a comma or a semicolon.

    A run longer than MAX_LEN with no comma in it comes back whole. There is
    nowhere sensible to cut, and cutting mid phrase sounds worse than a long
    repeat.
    """
    if len(piece) <= MAX_LEN:
        return [piece]

    chunks: list[str] = []
    current = ""
    for part in _SOFT_BREAK.split(piece):
        candidate = f"{current} {part}".strip()
        if current and len(candidate) > MAX_LEN:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


# Voices that ship with macOS. `say -v '?'` lists what is installed.
# In order of preference, best first. `speak.best_voice` takes the first one
# that is actually installed. The enhanced voices are a separate download in
# System Settings, so a machine that never got them falls back to the plain
# voice that ships with macOS.
VOICES = {
    "en": ("Samantha (Enhanced)", "Samantha"),
    "pt": ("Luciana",),
}

PT_WORDS = {"de", "que", "não", "para", "uma", "com", "do", "da",
            "em", "é", "os", "as", "por", "mais", "isso"}
EN_WORDS = {"the", "and", "of", "to", "is", "that", "for",
            "with", "this", "are", "it", "as", "be"}

MIN_MARKERS = 5   # under this many marker words, the guess is not worth trusting

_WORD = re.compile(r"[a-zà-ÿ]+")


def detect_lang(text: str, fallback: str = "en") -> str:
    """Guess the language of one section by counting marker words.

    The guess is made per section and not per document. A document written in
    one language often quotes a message in another, and a voice reading the
    wrong language is hard to follow.
    """
    words = _WORD.findall(text.lower())
    pt = sum(1 for w in words if w in PT_WORDS)
    en = sum(1 for w in words if w in EN_WORDS)
    if pt + en < MIN_MARKERS or pt == en:
        return fallback
    return "pt" if pt > en else "en"


# What the tool says instead of reading a code block or a table out loud.
SAY_CODE = {"en": "code block, {info}", "pt": "bloco de código, {info}"}
SAY_CODE_PLAIN = {"en": "code block", "pt": "bloco de código"}
SAY_TABLE = {"en": "Table, {n} columns.", "pt": "Tabela, {n} colunas."}
SAY_ROW = {"en": "Row {i}.", "pt": "Linha {i}."}

_FENCE = re.compile(r"^\s*(```|~~~)(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")


@dataclass
class _Raw:
    """One element found in the text, before it becomes speakable."""

    kind: str
    line: int
    lines: list[str]
    level: int = 0
    info: str = ""


def _tokenize(text: str) -> list[_Raw]:
    lines = text.splitlines()
    out: list[_Raw] = []
    i = 0

    # Front matter only counts when it opens on the very first line.
    if lines and lines[0].strip() == "---":
        j = 1
        while j < len(lines) and lines[j].strip() != "---":
            j += 1
        i = j + 1

    while i < len(lines):
        line = lines[i]

        if not line.strip() or _RULE.match(line):
            i += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            mark, info = fence.group(1), fence.group(2).strip()
            start, body, i = i, [], i + 1
            while i < len(lines) and not lines[i].strip().startswith(mark):
                body.append(lines[i])
                i += 1
            i += 1                                  # step over the closing fence
            out.append(_Raw("code", start + 1, body, info=info.split()[0] if info else ""))
            continue

        head = _HEADING.match(line)
        if head:
            out.append(_Raw("heading", i + 1, [head.group(2)], level=len(head.group(1))))
            i += 1
            continue

        # A table needs the separator row under the header, or a line that only
        # happens to start with a pipe would swallow the paragraph after it.
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            start, body = i, []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                body.append(lines[i])
                i += 1
            out.append(_Raw("table", start + 1, body))
            continue

        if _LIST_ITEM.match(line):
            start, body = i, []
            while i < len(lines) and _LIST_ITEM.match(lines[i]):
                body.append(_LIST_ITEM.match(lines[i]).group(1))
                i += 1
            out.append(_Raw("list", start + 1, body))
            continue

        start, body = i, []
        while i < len(lines) and lines[i].strip() and not _stops_prose(lines[i]):
            body.append(lines[i])
            i += 1
        out.append(_Raw("prose", start + 1, body))

    return out


def _stops_prose(line: str) -> bool:
    return bool(_HEADING.match(line) or _FENCE.match(line)
                or _LIST_ITEM.match(line) or _RULE.match(line))


def _table_sentences(raw: _Raw, lang: str) -> list[str]:
    rows = [[c.strip() for c in r.strip().strip("|").split("|")]
            for r in raw.lines if not _TABLE_SEP.match(r)]
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    out = [SAY_TABLE[lang].format(n=len(header))]
    for n, row in enumerate(body, start=1):
        pairs = ". ".join(f"{clean_inline(h)}: {clean_inline(c)}"
                          for h, c in zip(header, row))
        out.extend(hard_wrap(f"{SAY_ROW[lang].format(i=n)} {pairs}."))
    return out


def _render(raws: list[_Raw], read_code: bool, doc_lang: str) -> list[Block]:
    # Group by heading, so the language of a section reaches the code and
    # table announcements that sit inside it.
    groups: list[list[_Raw]] = []
    for raw in raws:
        if raw.kind == "heading" or not groups:
            groups.append([])
        groups[-1].append(raw)

    blocks: list[Block] = []
    previous = doc_lang
    for group in groups:
        sample = " ".join(" ".join(r.lines) for r in group if r.kind != "code")
        lang = detect_lang(sample, fallback=previous)
        previous = lang

        for raw in group:
            if raw.kind == "heading":
                sentences = [clean_inline(raw.lines[0])]
            elif raw.kind == "code":
                if read_code:
                    sentences = [s for line in raw.lines
                                 for s in hard_wrap(line.strip()) if line.strip()]
                elif raw.info:
                    sentences = [SAY_CODE[lang].format(info=raw.info)]
                else:
                    sentences = [SAY_CODE_PLAIN[lang]]
            elif raw.kind == "table":
                sentences = _table_sentences(raw, lang)
            elif raw.kind == "list":
                sentences = [s for item in raw.lines
                             for s in hard_wrap(clean_inline(item)) if clean_inline(item)]
            else:
                # Strip the quote marker per line. clean_inline only sees the
                # start of the string, so joining first leaves the markers of
                # every line after the first inside the text.
                lines = [_QUOTE.sub("", line) for line in raw.lines]
                sentences = split_sentences(clean_inline(" ".join(lines)))

            if sentences:
                blocks.append(Block(kind=raw.kind, line=raw.line, lang=lang,
                                    sentences=sentences, level=raw.level))
    return blocks


def parse(text: str, read_code: bool = False) -> list[Block]:
    """Turn markdown text into the list of blocks the player reads."""
    raws = _tokenize(text)
    # Code is left out of the count, the same way _render leaves it out of
    # each section's sample. Python and TypeScript keywords are English marker
    # words, so counting them makes a code heavy Portuguese document read as
    # English, and the first weak section then inherits the wrong voice.
    sample = " ".join(" ".join(r.lines) for r in raws if r.kind != "code")
    doc_lang = detect_lang(sample, fallback="en")
    return _render(raws, read_code, doc_lang)
