# wozmod

A fan mod for Musou Orochi Z, the Japanese Windows PC release (Koei Tecmo,
2009): English text, voices, movies and menu art, plus gameplay and display patches.
Players get a proxy `d3d9.dll` and a `wozmod/` folder beside the stock
`OROCHI_Z.exe`. The installer builds `wozmod/` on the player's machine from
their own copies of Musou Orochi Z, Warriors Orochi Xbox 360, and Warriors
Orochi 2 Xbox 360 and PSP (all USA). No stock file changes.

## Our own work only

The repository and every release hold only our own work: code, translations we
wrote, recipes, hashes, and pointers that name an item in a stock or donor file.
Game content (Japanese text, official English text, art, audio, archive entries
and anything built from them) enters only at build time, read from the player's
copies.

- Key translation rows by a hash of the stock string. A row that uses official
  English stores a pointer to the donor string, never the text.
- In docs, comments and tests, name game text by its id or pointer, not by
  quoting it.
- One exception, by the owner's decision: `data/text/dub_captions.json` holds
  word-for-word transcripts of the English dub in the four WO1 ending movies,
  which no disc captions in English. Add no other transcript without asking.
- `tests/test_repository_hygiene.py` goes red on game files, Japanese text and
  byte dumps. Fix the content that trips it rather than adding an exception.

## Layout

| Path | Holds |
|---|---|
| `runtime/` | C source of `d3d9.dll` |
| `src/wozmod/`, `native/` | build and install tooling (Python, with Rust for the slow parts) |
| `data/` | our authored data: translations, layout rules, art recipes, donor pointers |
| `tools/` | developer-only tools such as the game harness |
| `packaging/` | release zips and the release files `lettering.zip` and `d3d9.dll` |
| `work/` | ignored: stock and donor extracts, builds, disposable game copies |

## Inputs and the game

- Stock and donor files are read-only inputs. Extract and build only under
  `work/`.
- Donor priority, per item: menu art and cinematics use WO2 X360, then
  WO1 X360. Dialogue voices use WO2 X360, then WO1 X360, then WO2 PSP, whose
  lower-quality takes fill only lines neither Xbox 360 disc has. Fonts, attack
  shouts and text use WO1 X360, then WO2 X360, then WO2 PSP. Our authored work fills the gaps, including PC-only
  text such as keyboard and graphics settings. The layout reference (font
  size, boxes) is WO2 X360 only. Music stays stock, except the mix inside
  donor movies.
- Launch the game only with the user's permission for the current task, in a
  disposable copy under `work/`, through `tools/run_check.py` or
  `tools/game_harness.py run --slot` (two games at most, across all worktrees).
  Touch a live installation only when the user names its path and the operation.
- Test in the game against one install per checkout: `work/install/`, a
  reflink copy of the stock game that `wozmod install` wrote into. Reinstall
  into that folder only when the installer's output changes; for a runtime or
  single-file change, pass the new build to `tools/run_check.py` with `--dll`
  or `--file`. Do not make another install per task or per round: each one
  costs about 3 GB of disk.
- When the disk needs it, `tools/cleanup.py` lists old game test runs, stale
  caches, older packaging rounds and worktrees already in main; `--delete`
  removes them slowly. Run it by hand only, never from a hook or a timer.

## Testing

Run `uv run pytest -q` (6 workers by default; `-n0` for one). Bugs worth naming
here are a shifted offset, a dropped translation row, and an edit over the wrong
stock bytes. Synthetic bytes in
`tmp_path` cover formats and patch logic. Use stock or donor files only for
what synthetic bytes cannot show, and skip when they are absent. Slow tests run
only behind a `WOZMOD_<NAME>=1` skipif. Input paths come from `[tests]` in
the main checkout's `work/harness.toml` (see `tests/conftest.py`).
`tools/verify.sh` also runs the slow ones, and fails when an input is missing
instead of skipping. Before handing back a change to the runtime, the art or the
install, run the slow tests that guard what you changed: `tools/verify.sh`
followed by their paths (for example the DLL pin in `tests/test_install_pins.py`
after a runtime change). Run `tools/verify.sh --full`, which runs everything and
rebuilds the art even when nothing it reads has changed, before a release. Hand back a
change players see with the
`evidence/contact-sheet.html` that `tools/run_check.py` writes: each capture beside
its PASS/FAIL rows. The hooks in `tools/githooks/` lint each
commit (`git config core.hooksPath tools/githooks` on a new clone).

## Commits

Write each one as a short public changelog entry. This format overrides any user-level commit rule.

```
Show the version and redesign the setup window

Players who are not developers could not tell what to pick.

Changes:
- Add -v/--version, and show the version in the window's corner.
- Say why Install is disabled, and what to do after an error.
```

- Subject: sentence case, imperative, at most 72 characters.
- Why: at most two sentences, about what changes for players or contributors.
- Changes: 2 to 6 lines, one behaviour each, in words a player could follow,
  with no blank lines between them.
- Keep test counts, file lists, to-dos, hashes and how the work was organised
  (agents, branches, phases, migrations) in the agent's hand-back.
- Work on a branch merges into main as one squashed commit in this format.

## Root causes

Fix every defect at its root cause: trace the symptom back to the code, data
or asset that produces it, find out why it is that way, and change that. A
change that only offsets the symptom (a compensating shift, a special case, a
clamp, a widened tolerance, a test exception) is a workaround, and workarounds
are not allowed, even when the result looks right. When the root cause is out of
reach or the real fix needs a decision, report what you found and ask.

## Decisions

Make implementation and workflow decisions yourself. Ask the user about
anything players see or anything that changes what the repo publishes.
