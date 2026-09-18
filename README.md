# readtome

Read a markdown file out loud, using the `say` command that ships with macOS.

It cleans the markdown first, so code blocks and tables do not turn into noise.
It picks the voice from the language of each section. It tells you how long the
reading will take before the first word, so you know what you are starting.
While it plays, one key pauses, jumps or changes the speed.

## Why I made this

I don't know about you, but working with AI has me reading a LOT more than I
used to. My eyes are tired and my head is tired.

BTW, if you don't know me: I only really learned to read after I turned 7, and
it was my mum, with a lot of patience, who got me there. I have ADHD, the real
kind with a diagnosis and everything, and reading is one of the parts it hits
hardest.

So that is what readtome is for. Claude Code writes the spec, the plan and the
design doc, and then I have to read all of it to know if it understood what I
asked for. Now I listen to it instead.

It only runs on macOS today. Feel free to contribute and change whatever you
need for the way you work.

## Install

```
brew install nathpaiva/tap/readtome
```

Or clone this repository and make one symlink. The tool uses the Python
standard library only, so there is nothing else to install:

```
mkdir -p ~/.local/bin
ln -sfn "$PWD/cli.py" ~/.local/bin/readtome
```

[fzf](https://github.com/junegunn/fzf) is optional. With it, the branch and
file menus open in fzf. Without it, they print numbered rows.

```
brew install fzf
```

To use `-p <name>`, tell readtome where your repos live:

```
export READTOME_ROOTS="$HOME/code:$HOME/work"
```

Without that variable everything still works, except looking a project up by
name.

## Use

```
readtome                             # this repo, this branch, pick a file
readtome README.md                   # read this file
readtome -B                          # pick a branch, then pick a file
readtome -p my-repo -b my-branch
readtome docs/spec.md --list
readtome docs/spec.md --from "error handling" -r 260
readtome docs/spec.md --from 41
readtome docs/spec.md --resume
```

## How long it takes

Before the first word, readtome prints the length of what you are about to
hear:

```
$ readtome docs/plans/some-plan.md
about 14 min at r220
→    1  # Some Plan
 ▶    1  sentence 1/1   r220
```

It counts from where the reading starts, so `--from` shortens it. The rate is
in the line because the number assumed that rate. Pressing `+` or `-` later
changes the real length and does not redraw the estimate.

`say -r 220` does not deliver 220 words a minute. It runs about 7% slower than
that on real text, and readtome allows for it. Expect the number to be right
within about a tenth.

## Flags

| Flag | What it does | Default |
|---|---|---|
| `<file>` | read this path, ignoring every other source flag | none |
| `-p`, `--project` | repo name or path | the repo you are in |
| `-b`, `--branch` | branch to read from | the current branch |
| `-B` | show the branches and pick one | off |
| `-r`, `--rate` | words per minute | 220 |
| `--from` | start at a heading, or at a line number | the top |
| `--list` | print the headings with their lines, then stop | off |
| `--code` | read the code blocks instead of skipping them | off |
| `--voice` | one macOS voice, turns detection off | best installed |
| `--resume` | continue where this file stopped last time | off |

A `--from` line number past the end of the file is an error, not a silent
rewind to the top.

## Voices

English uses **Samantha (Enhanced)** when you have it, and plain **Samantha**
when you do not. Portuguese uses **Luciana**. The enhanced voices sound less
robotic and are a free download: System Settings, Accessibility, Spoken
Content, System Speech Voice, Manage Voices.

readtome asks `say` which voices are installed and takes the best one it wants.
Nothing breaks on a machine without the download. `say -v '?'` lists every name
you can pass.

To keep your own voices, set them once:

```
export READTOME_VOICE_EN="Ava (Premium)"
export READTOME_VOICE_PT="Luciana"
```

Each language keeps its own voice, so a document that mixes the two still
changes voice as it reads. `--voice <name>` beats both and turns language
detection off, which is what you want for one run and not for every run.

A name that is not installed stops readtome with a message. `say` would take
it, exit 0, and read the whole document in the default voice, which looks like
the variable did nothing.

## Picking a branch or a file

With [fzf](https://github.com/junegunn/fzf) installed, `-B` and the file menu
open in it: arrows move, typing filters, Enter picks, esc cancels. In fzf's
layout the first row is at the bottom, so up walks into the list.

fzf is optional. Without it, the same menus print numbered rows and ask for a
number.

## Keys while it plays

| Key | What it does |
|---|---|
| space | pause, then play again from the top of that sentence |
| ↓ | the next sentence |
| ↑ | the sentence before |
| `n` | jump to the next heading |
| `b` | jump to the previous heading |
| `+` / `-` | change the speed by 20 words per minute |
| `=` / `_` | the same, for when shift is held or not |
| `q` | quit, saving the position |

↑ at the first sentence of a paragraph goes to the **last** sentence of the
one before, so ↑ undoes ↓. At the very start of the file ↑ does nothing, rather
than dropping you out of the reading.

While it is paused only space and `q` do anything. The other keys wait until it
is playing again.

**The keys need a real terminal.** Run `readtome` in Terminal, iTerm or your
shell. Run it through something that captures the output, such as the `!`
prefix in Claude Code, and there is no terminal to type into: readtome reads
the whole file with no keys and no way to stop. It prints a line saying so
when that happens.

## Tests

```
python3 -m unittest discover -s tests -t . -v
```
