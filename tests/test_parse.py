"""Tests for readtome/parse.py.

parse.py is a pure function, so these tests need no git, no audio and no
temporary files.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parse  # noqa: E402


class CleanInlineTest(unittest.TestCase):
    def test_link_keeps_only_the_text(self):
        self.assertEqual(parse.clean_inline("see [the docs](http://x.com) now"),
                         "see the docs now")

    def test_image_says_the_word_image(self):
        self.assertEqual(parse.clean_inline("![a chart](chart.png)"),
                         "image, a chart")

    def test_image_with_no_alt_text(self):
        self.assertEqual(parse.clean_inline("![](chart.png)"), "image")

    def test_bold_and_italic_lose_their_marks(self):
        self.assertEqual(parse.clean_inline("**bold** and *thin*"),
                         "bold and thin")

    def test_backticks_keep_the_word(self):
        self.assertEqual(parse.clean_inline("run `git status` first"),
                         "run git status first")

    def test_underscore_becomes_a_space(self):
        self.assertEqual(parse.clean_inline("set READTOME_ROOTS now"),
                         "set READTOME ROOTS now")

    def test_html_comment_disappears(self):
        self.assertEqual(parse.clean_inline("keep <!-- drop me --> this"),
                         "keep this")

    def test_quote_marker_is_dropped(self):
        self.assertEqual(parse.clean_inline("> a quoted line"), "a quoted line")

    def test_repeated_spaces_collapse(self):
        self.assertEqual(parse.clean_inline("two    spaces"), "two spaces")


class BlockTest(unittest.TestCase):
    def test_block_defaults(self):
        b = parse.Block(kind="prose", line=4)
        self.assertEqual(b.lang, "en")
        self.assertEqual(b.sentences, [])
        self.assertEqual(b.level, 0)


class SplitSentencesTest(unittest.TestCase):
    def test_splits_on_a_full_stop(self):
        self.assertEqual(
            parse.split_sentences("The first one here. The second one here."),
            ["The first one here.", "The second one here."],
        )

    def test_splits_on_question_and_exclamation(self):
        self.assertEqual(
            parse.split_sentences("Is this the first one? Yes it really is!"),
            ["Is this the first one?", "Yes it really is!"],
        )

    def test_a_short_piece_joins_the_next_one(self):
        # "Ok." is 3 characters, under MIN_LEN, so it must not be spoken alone.
        self.assertEqual(
            parse.split_sentences("Ok. That is the whole answer here."),
            ["Ok. That is the whole answer here."],
        )

    def test_a_long_piece_splits_at_a_comma(self):
        piece = ("one part that is quite long here, " * 8).strip().rstrip(",") + "."
        # Guard the guard: at 4 repeats this piece is 135 characters, under
        # MAX_LEN, so the test would pass whatever hard_wrap does.
        self.assertGreater(len(piece), parse.MAX_LEN)
        out = parse.split_sentences(piece)
        self.assertGreater(len(out), 1)
        self.assertTrue(all(len(p) <= parse.MAX_LEN for p in out))

    def test_a_long_piece_with_no_comma_stays_whole(self):
        piece = "word " * 60
        out = parse.split_sentences(piece)
        self.assertEqual(len(out), 1)

    def test_empty_text_gives_no_sentence(self):
        self.assertEqual(parse.split_sentences("   "), [])


class DetectLangTest(unittest.TestCase):
    def test_plain_english(self):
        text = "This is the part of the screen that is used to show the report."
        self.assertEqual(parse.detect_lang(text), "en")

    def test_plain_portuguese(self):
        text = "Essa e a parte da tela que e usada para mostrar o relatorio com os dados."
        self.assertEqual(parse.detect_lang(text), "pt")

    def test_portuguese_with_accents(self):
        # Every marker here carries an accent: "não" three times and "é" three
        # times, six in all. The other words are not markers in either list.
        # Narrow the word regex to ASCII and this drops to zero markers, so the
        # answer falls back to "en" and this test fails. That is its whole job.
        text = "Ela não é rápida, não é lenta, não é nada."
        self.assertEqual(parse.detect_lang(text, fallback="en"), "pt")

    def test_too_few_marker_words_keeps_the_fallback(self):
        self.assertEqual(parse.detect_lang("Retry button", fallback="pt"), "pt")

    def test_one_marker_is_not_enough_to_switch(self):
        # pt=1, en=0. Only MIN_MARKERS can decide this one: with no floor the
        # single "de" would be enough to switch the voice.
        self.assertEqual(parse.detect_lang("Retry de button", fallback="en"), "en")

    def test_a_tie_keeps_the_fallback(self):
        # Three english markers (the, of, and) against three portuguese ones
        # (de, que, para). Six in all, so the MIN_MARKERS floor is cleared and
        # only the tie can decide. Drop `or pt == en` and this test fails.
        self.assertEqual(parse.detect_lang("the of and de que para", fallback="pt"), "pt")


DOC = """\
---
title: ignored
---

# Presence report

The screen loads the report and it shows the data that is in the account.

## Data flow

```typescript
const x = 1;
```

| Flag | What it does | Default |
|---|---|---|
| -r | speed | 220 |

- first item
- second item

## Em portugues

Não é para mostrar isso na tela, com os dados de uma conta que não existe mais.
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.blocks = parse.parse(DOC)

    def test_front_matter_is_dropped(self):
        self.assertNotIn("ignored",
                         " ".join(s for b in self.blocks for s in b.sentences))

    def test_block_kinds_in_order(self):
        self.assertEqual([b.kind for b in self.blocks],
                         ["heading", "prose", "heading", "code",
                          "table", "list", "heading", "prose"])

    def test_line_numbers_point_at_the_real_lines(self):
        self.assertEqual([b.line for b in self.blocks],
                         [5, 7, 9, 11, 15, 19, 22, 24])

    def test_heading_level_and_text(self):
        self.assertEqual(self.blocks[0].level, 1)
        self.assertEqual(self.blocks[0].sentences, ["Presence report"])
        self.assertEqual(self.blocks[2].level, 2)

    def test_code_is_announced_and_not_read(self):
        self.assertEqual(self.blocks[3].sentences, ["code block, typescript"])

    def test_code_is_read_when_asked(self):
        blocks = parse.parse(DOC, read_code=True)
        code = [b for b in blocks if b.kind == "code"][0]
        self.assertEqual(code.sentences, ["const x = 1;"])

    def test_table_repeats_the_column_name(self):
        self.assertEqual(
            self.blocks[4].sentences,
            ["Table, 3 columns.",
             "Row 1. Flag: -r. What it does: speed. Default: 220."],
        )

    def test_list_drops_the_dash(self):
        self.assertEqual(self.blocks[5].sentences, ["first item", "second item"])

    def test_english_section_uses_the_english_voice(self):
        self.assertEqual(self.blocks[1].lang, "en")

    def test_portuguese_section_switches_language(self):
        self.assertEqual(self.blocks[7].lang, "pt")

    def test_a_weak_section_inherits_the_one_before_it(self):
        # "Data flow" plus a table and a short list hold under MIN_MARKERS
        # marker words, so the section keeps the language of the one above.
        self.assertEqual(self.blocks[4].lang, "en")

    def test_the_announcement_follows_the_section_language(self):
        # Two sections on purpose. The document as a whole reads as English,
        # 11 English markers against 9 Portuguese, but the section that holds
        # the code block reads as Portuguese. With one section only, the
        # document language and the section language are the same sum over the
        # same words, so swapping one for the other could not be caught.
        pt_doc = ("## English first\n\nThis is the part of the screen that is "
                  "used to show the report and the data.\n\n"
                  "## Em portugues\n\nNão é para mostrar isso na tela, com os "
                  "dados de uma conta.\n\n```python\nx = 1\n```\n")
        self.assertEqual(parse.detect_lang(pt_doc), "en")
        code = [b for b in parse.parse(pt_doc) if b.kind == "code"][0]
        self.assertEqual(code.lang, "pt")
        self.assertEqual(code.sentences, ["bloco de código, python"])

    def test_a_weak_section_inherits_a_portuguese_one(self):
        # DOC only ever inherits English, so forcing the fallback to "en"
        # changes nothing there and the inheritance rule goes untested. The
        # mutation pass found this: row 6 turned no test red. Here the weak
        # section follows a Portuguese one, and the code announcement shows
        # which language it took.
        doc = ("## Em portugues\n\nNão é para mostrar isso na tela, com os "
               "dados de uma conta que não existe mais.\n\n"
               "## Data flow\n\nRetry button.\n\n```python\nx = 1\n```\n")
        code = [b for b in parse.parse(doc) if b.kind == "code"][0]
        self.assertEqual(code.lang, "pt")
        self.assertEqual(code.sentences, ["bloco de código, python"])

    def test_the_document_language_ignores_code(self):
        # Code keywords are English marker words. Counting them makes a code
        # heavy Portuguese document read as English, and the first weak
        # section then inherits the wrong voice. Measured on this document:
        # with the code counted the answer is "en", without it "pt".
        doc = ("## Nota\n\nRetry aqui.\n\n```python\n"
               "for a in this and that:\n"
               "    b = [x for x in a if x is not None]\n"
               "    c = to be or not to be with the of and is that this are it as\n"
               "```\n\n"
               "## Detalhes\n\nNão é para mostrar isso na tela, com os dados "
               "de uma conta.\n")
        blocks = parse.parse(doc)
        self.assertEqual(blocks[0].lang, "pt")
        code = [x for x in blocks if x.kind == "code"][0]
        self.assertEqual(code.sentences, ["bloco de código, python"])

    def test_a_quote_loses_its_marker_on_every_line(self):
        doc = "> first line of the quote\n> second line of the quote\n"
        spoken = " ".join(parse.parse(doc)[0].sentences)
        self.assertNotIn(">", spoken)

    def test_a_tilde_fence_is_a_code_block(self):
        blocks = parse.parse("~~~bash\necho hi\n~~~\n")
        self.assertEqual([b.kind for b in blocks], ["code"])
        self.assertEqual(blocks[0].sentences, ["code block, bash"])

    def test_the_other_list_markers_work(self):
        doc = "* star item\n+ plus item\n\n1. first numbered\n2) second numbered\n"
        blocks = parse.parse(doc)
        self.assertEqual([b.kind for b in blocks], ["list", "list"])
        self.assertEqual(blocks[0].sentences, ["star item", "plus item"])
        self.assertEqual(blocks[1].sentences, ["first numbered", "second numbered"])

    def test_a_pipe_line_with_no_separator_row_is_prose(self):
        # The guard in _tokenize exists for exactly this line. Drop the
        # _TABLE_SEP check and this turns into a table block, which is the
        # bug the guard was written to stop.
        doc = "| this line starts with a pipe\nbut the next line is not a separator.\n"
        self.assertEqual([b.kind for b in parse.parse(doc)], ["prose"])

    def test_a_horizontal_rule_is_dropped(self):
        blocks = parse.parse("one two three the of and is\n\n---\n\nfour five\n")
        self.assertEqual([b.kind for b in blocks], ["prose", "prose"])

    def test_an_empty_document_gives_no_block(self):
        self.assertEqual(parse.parse(""), [])


if __name__ == "__main__":
    unittest.main()
