# wozmod

A fan mod for Musou Orochi Z, the Japanese Windows PC release (Koei Tecmo,
2009): English text, voices and menu art, plus gameplay and display patches.
Players get a proxy `d3d9.dll` and a `wozmod/` folder beside the stock
`OROCHI_Z.exe`. The installer builds `wozmod/` on the player's machine from
their own copies of Musou Orochi Z, Warriors Orochi PC (Europe), and Warriors
Orochi 2 Xbox 360 and PSP (both USA). No stock file changes.

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
- `tests/test_repository_hygiene.py` goes red on game files, Japanese text and
  byte dumps. Fix the content that trips it rather than adding an exception.

## Layout

| Path | Holds |
|---|---|
| `runtime/` | C source of `d3d9.dll` |
| `src/wozmod/` | build and install tooling |
| `data/` | our authored data: translations, layout rules, art recipes, donor pointers |
| `tools/` | developer-only tools such as the game harness |
| `work/` | ignored: stock and donor extracts, builds, disposable game copies |

## Inputs and the game

- Stock and donor files are read-only inputs. Extract and build only under
  `work/`.
- Donor priority, per item: menu art, dialogue voices and cinematics use
  X360, then WO1 PC. Fonts, attack shouts and text use WO1 PC, then X360,
  then PSP. Our authored work fills the gaps. The layout reference (font size,
  boxes) is X360 only. Music stays stock.
- Launch the game only with the user's permission for the current task, in a
  disposable copy under `work/`. Touch a live installation only when the user
  names its path and the operation.

## Testing

Run `uv run pytest -q`. Bugs worth naming here are a shifted offset, a dropped
translation row, and an edit over the wrong stock bytes. Synthetic bytes in
`tmp_path` cover formats and patch logic. Use stock or donor files only for
what synthetic bytes cannot show, and skip when they are absent. Slow tests run only behind a `WOZMOD_<NAME>=1` skipif.
Input paths come from `[tests]` in `work/harness.toml` (see `tests/conftest.py`).
`tools/verify.sh` also runs the slow ones; run it before handing back a change to
the runtime, the art or the install. The hooks in `tools/githooks/` lint each
commit and test each push on the pushed commits alone, in `.worktrees/pre-push`
(`git config core.hooksPath tools/githooks` on a new clone).

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

## Decisions

Make implementation and workflow decisions yourself. Ask the user about
anything players see or anything that changes what the repo publishes.
