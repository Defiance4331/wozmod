# wozmod

wozmod is an unofficial fan mod for **Musou Orochi Z**, the Japanese PC
release of Warriors Orochi Z. It translates the game into English and adds new
options.

> **Legal notice:** wozmod is an unofficial fan project. It is not affiliated
> with, endorsed by, or sponsored by Koei Tecmo. wozmod does not include or
> distribute game assets: no text, art, audio, video or program code from
> Musou Orochi Z or from the games it reads. You must own copies of all four
> games and supply the files yourself. wozmod contains no decryption code, and
> this project does not help you get game files or decryption programs.

> **Status:** wozmod is in development. Expect bugs.

## What you get

- **English text** in menus, battles and story scenes. Most lines are the
  official English from Warriors Orochi and Warriors Orochi 2. We translated
  the rest.
- **English voices** from the same games. Lines with no English recording keep
  their Japanese voice.
- **English menu art** in the style of the original menus.
- **A language switch.** Choose English or Japanese for text and for voices.
- **120 FPS** in battle.
- **A wider field of view**, up to 150%.
- **Xbox controller support** with the Warriors Orochi 2 controls and Xbox
  button prompts.

wozmod does not change the game's own files. It adds a file, `d3d9.dll`, and a
folder, `wozmod`, to the game folder.

## What you need

1. **Musou Orochi Z for Windows** (the Japanese release), a copy you own,
   installed. wozmod checks the game files first. If they are a different
   version, wozmod changes nothing.
2. **Your own copies of these three games:**
   - **Warriors Orochi for PC** (the European release), as a disc image
     (`.iso`).
   - **Warriors Orochi 2 for Xbox 360** (the US release), as a disc image
     (`.iso`).
   - **Warriors Orochi 2 for PSP** (the US release), as a disc image (`.iso`).

   wozmod reads the official English text, art and voices from these games
   when you install it. We cannot include them, because they belong to Koei
   Tecmo. You need all three.
3. **A decryption program for each of the two encrypted files.** The
   program files of the two Warriors Orochi 2 games are encrypted on the
   disc, and wozmod does not decrypt files. Bring a decryption program you
   already have for each one, and wozmod runs it on the file from your disc
   image:

   | Game | File on the disc image |
   |---|---|
   | Warriors Orochi 2 for Xbox 360 | `default.xex` |
   | Warriors Orochi 2 for PSP | `PSP_GAME/SYSDIR/EBOOT.BIN` |

   If you already have a decrypted file, you can give it instead, under
   **Advanced** in the window, or with `--x360-file` and `--psp-file` in the
   terminal. If a disc image already holds the decrypted file, wozmod uses it
   and needs no program for that game.

   wozmod checks what the program makes against a fixed hash. If it is not
   the file wozmod needs, wozmod says so before it changes anything.

   Optional note: these tools have been tested to produce files that wozmod
   accepts: pspdecrypt 1.0 for the PSP EBOOT, and xex2ool 0.3.0 and XexTool
   7.0 for the Xbox 360 program file. We do not ship, provide or endorse
   any tool, and we make no legal claim about using one. wozmod only runs
   the program you choose, on files from your own disc images.
4. **Windows 10 or later**, or **Linux** with Steam (Proton) or Wine.

## Install

1. Download the release for your system (Windows or Linux) from the
   **Releases** page, and unzip it to any folder. It holds two programs,
   `wozmod-setup` and `wozmod`.
2. Open `wozmod-setup`. It shows a window.
3. Pick your files. Click **Browse** next to each one:
   - the Musou Orochi Z folder;
   - the disc image of each of the three games;
   - the decryption program for the Xbox 360 file, and the one for the PSP
     file, from "What you need".

   wozmod checks each file when you pick it. For a decryption program, it
   runs the program on the file from your disc image. A tick means the file
   is right. If it is wrong, the window says why.
4. Click **Install**. wozmod builds the English files on your computer. This
   takes about two minutes. The window shows the progress and the time left,
   and **Cancel** stops the install and leaves the game folder as it was.
   When it is done, the window says so.

wozmod remembers your files for next time. To update, unzip the new release
and open its `wozmod-setup`. The button then says **Update**, and your
settings stay. To remove wozmod, click **Uninstall** in the same window.

If you prefer a terminal, open one in the release folder, and run `wozmod`.
It asks for each file. Or give them all in one command:

```
wozmod install --game "<Musou Orochi Z folder>" \
  --wo1 <Warriors Orochi .iso> --x360 <Warriors Orochi 2 360 .iso> \
  --psp <Warriors Orochi 2 PSP .iso> \
  --x360-tool <decryption program for default.xex> \
  --psp-tool <decryption program for EBOOT.BIN>
```

Advanced: instead of `--x360-tool` or `--psp-tool`, you can give a file that
is already decrypted with `--x360-file` or `--psp-file`. For each game, give
one of the two. If you give both, wozmod uses the file and does not run the
program.

To use fewer processor cores, set `WOZMOD_JOBS`, for example `WOZMOD_JOBS=2`.

### Extra steps on Linux (Steam or Proton)

Wine ignores wozmod unless you tell it to load it. Add this line to the
**Launch Options** of the game in Steam:

```
WINEDLLOVERRIDES="d3d9=n,b" DXVK_CONFIG="d3d9.cachedWriteOnlyBuffers = True" %command%
```

The `DXVK_CONFIG` part is for the game itself, with or without wozmod. Without
it, some battles slow down to one frame per second.

## Settings

Open **Options**, then **Game Settings**. wozmod adds these rows:

| Row | What it does |
|---|---|
| TEXT LANGUAGE | English or Japanese. The change applies the next time you start the game. |
| VOICE LANGUAGE | English or Japanese. The change applies from the next line. This row shows only if you installed the English voices. |
| FIELD OF VIEW | 100% (original) to 150%. |
| FRAME RATE | 60 or 120 FPS in battle. |

wozmod keeps these settings in `wozmod/settings.ini`. Your save file does not
change.

## Uninstall

Run `wozmod uninstall --game "<Musou Orochi Z folder>"`. Or delete
`d3d9.dll` and the `wozmod` folder from the game folder. Your game and your
saves stay as they were.

## Problems

- **Windows says "Windows protected your PC" when you open wozmod.** wozmod
  is not signed, so Windows does not know the publisher. Click **More info**,
  then **Run anyway**.
- **The game is still in Japanese, and the new settings are missing.** The game
  did not load wozmod. Make sure that `d3d9.dll` is in the same folder as
  `OROCHI_Z.exe`. On Linux, add the launch options above.
- **`wozmod/runtime.log` says "refused".** Your `OROCHI_Z.exe` is not the
  version that wozmod supports. If you installed a different English patch,
  reinstall the game. Then install wozmod again.
- **The screen stays black at start (Linux).** The game must start in its own
  folder. If you added it to Steam as a non-Steam game, set **Start In** to the
  game folder.

## Build from source

Install [uv](https://docs.astral.sh/uv/), [ffmpeg](https://ffmpeg.org/) and
[Rust](https://rustup.rs). In the repository folder, run
`uv run --extra native wozmod install ...` with the options above, plus
`--lettering` and `--dll` (the two release files, which the repository does
not hold). `uv run wozmod-setup` opens the window. Without `--extra native`,
wozmod uses its slower Python code, and builds the same files.

To build the release zip for your system, run
`uv sync --extra native --group build`, then
`uv run python packaging/build.py --assets <folder with d3d9.dll and lettering.zip>`.
`packaging/release_assets.py` explains how those two files are made and
published, and the release workflow in `.github/workflows/` builds the zips
for Windows and Linux when a `v*` tag is pushed.

## Legal

wozmod is an unofficial fan-made modification for Musou Orochi Z. It is not
affiliated with, endorsed by, or sponsored by Koei Tecmo. Game and company
names identify compatibility and the games that wozmod needs. This repository
holds no files from the games, and you need your own copies to use it.
The installer reads your copies on your own computer and builds the English
files there. Nothing from the games leaves your computer or comes with wozmod.

The English menu lettering is original artwork: vector letters drawn for
wozmod to match the look of the official menus, which the release ships
rendered. It contains no font files or images from any game, and it is
covered by the same licence as the rest of wozmod.

wozmod is free software under the [GNU General Public License v3.0 or later](LICENSE).
