# readtome

Read a markdown file out loud, using the `say` command that ships with macOS.

It cleans the markdown first, so code blocks and tables do not turn into noise.
It picks the voice from the language of each section. While it plays, one key
pauses, jumps or changes the speed.

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
Nothing breaks on a machine without the download. `--voice <name>` overrides
all of it, and `say -v '?'` lists every name you can pass.

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
| `n` | jump to the next heading |
| `b` | jump to the previous heading |
| `+` / `-` | change the speed by 20 words per minute |
| `=` / `_` | the same, for when shift is held or not |
| `q` | quit, saving the position |

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
