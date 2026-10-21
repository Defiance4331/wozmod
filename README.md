# wozmod

wozmod is an unofficial fan mod for **Musou Orochi Z**, the Japanese PC
release of Warriors Orochi Z. It translates the game into English and adds new
options.

> [!IMPORTANT]
> **Legal notice:** wozmod is an unofficial fan project. It is not affiliated
> with, endorsed by, or sponsored by Koei Tecmo. wozmod does not include or
> distribute game assets: no text, art, audio, video or program code from
> Musou Orochi Z or from the games it reads. You must own copies of all four
> games and supply the files yourself. wozmod contains no decryption code, and
> this project does not help you get game files or decryption programs.

> [!WARNING]
> **Status:** wozmod is in development. Expect bugs.

## What you get

- **English text** in menus, battles and story scenes. Most lines are the
  official English from Warriors Orochi and Warriors Orochi 2. The rest was
  translated for wozmod.
- **English voices** from the same games. Lines with no English recording keep
  their Japanese voice.
- **English menu art** in the style of the original menus, drawn sharper on
  large screens.
- **English story movies** from the Xbox 360 games, where they have English
  voices or text.
- **A language switch.** Choose English or Japanese for text and for voices.
- **120 FPS** in battle.
- **A wider field of view**, up to 150%.
- **More screen sizes**, up to the size of your monitor, and a borderless
  window mode.
- **Xbox controller support** with the Warriors Orochi 2 controls and Xbox
  button prompts. A new controller is set up at once, with the camera on the
  right stick.
- **Closing the game window quits the game** at once.

## What you need

1. **Musou Orochi Z for Windows** (the Japanese release), a copy you own,
   installed. wozmod checks the game files first. If they are a different
   version, wozmod changes nothing.
2. **Your own copies of these three games:**
   - **Warriors Orochi for Xbox 360** (the US release), as a disc image
     (`.iso`).
   - **Warriors Orochi 2 for Xbox 360** (the US release), as a disc image
     (`.iso`).
   - **Warriors Orochi 2 for PSP** (the US release), as a disc image (`.iso`).

   wozmod reads the official English text, art and voices from these games
   when you install it. They cannot be included, because they belong to Koei
   Tecmo. You need all three.
3. **A decryption program for the Xbox 360 files, and one for the PSP
   file.** The program files of the three games are encrypted on the disc,
   and wozmod does not decrypt files. One program serves both Xbox 360
   discs. Bring the programs, and wozmod runs each one on the file from your
   disc image:

   | Game | File on the disc image |
   |---|---|
   | Warriors Orochi for Xbox 360 | `default.xex` |
   | Warriors Orochi 2 for Xbox 360 | `default.xex` |
   | Warriors Orochi 2 for PSP | `PSP_GAME/SYSDIR/EBOOT.BIN` |

   wozmod was tested with the programs listed under
   [Tested decryption programs](#tested-decryption-programs). Other programs
   may not work.

   If a disc image already holds the decrypted file, wozmod uses it and needs
   no program for that game. If you already have a decrypted file, you can
   give it instead of the program.

   wozmod checks what the program makes against a fixed hash. If it is not
   the file wozmod needs, wozmod says so before it changes anything.
4. **Windows 10 or later**, or **Linux** with Steam (Proton) or Wine.

### Tested decryption programs

These programs have been tested to produce files that wozmod accepts:

| Game file | Program |
|---|---|
| Warriors Orochi 2 for PSP, `EBOOT.BIN` | pspdecrypt |
| Warriors Orochi for Xbox 360, `default.xex` | xex2ool or XexTool |
| Warriors Orochi 2 for Xbox 360, `default.xex` | xex2ool or XexTool |

The installer checks the file a program produces, so any version that gives
the right file works, and a wrong file is refused.

No tool is shipped, provided or endorsed with wozmod, and no legal claim is made
about using one. wozmod only runs the program you choose, on files from your own
disc images.

## Install

1. Download the release for your system (Windows or Linux) from the
   **Releases** page, and unzip it to any folder. It holds two programs that
   do the same job: `wozmod-setup`, a window, and `wozmod`, for a terminal.
   Most players want `wozmod-setup`. To use the terminal instead, see
   [Use the terminal instead](#use-the-terminal-instead).
2. Open `wozmod-setup`.
3. Pick your files. Click **Browse...** next to each one:
   - **1. Your game:** the Musou Orochi Z folder.
   - **2. Your disc images:** the disc image of each of the three games.
   - **3. Your decryption programs:** the program for the Xbox 360 files,
     and the one for the PSP file, from "What you need".

   wozmod checks each file when you pick it. For a decryption program, it
   runs the program on the file from your disc image. A tick means the file
   is right. If it is wrong, the window says why. To give an already
   decrypted file instead of a program, open
   **Advanced: use decrypted files instead**.
4. Click **Install**. wozmod builds the English files on your computer. This
   takes about five minutes, longer on a slow disk. The window shows the progress and the time left.
   Until wozmod starts placing files, **Cancel** stops the install and leaves
   the game folder as it was. When it is done, the window says so.
5. On Linux, also follow [Extra steps on Linux](#extra-steps-on-linux).

Then start the game as usual, and choose your languages in
[Settings](#settings). wozmod remembers your files for next time.

### Extra steps on Linux

Wine ignores wozmod unless you tell it to load it. Add this line to the
**Launch Options** of the game in Steam:

```
WINEDLLOVERRIDES="d3d9=n,b" DXVK_CONFIG="d3d9.cachedWriteOnlyBuffers = True" %command%
```

The `DXVK_CONFIG` part is for the game itself, with or without wozmod. Without
it, some battles slow down to one frame per second.

### Update

Unzip the new release and open its `wozmod-setup`. Your files are already
filled in, and the button says **Update from** your version **to** the new
one. Your settings stay.

### Uninstall

Open `wozmod-setup` and click **Uninstall**. It asks whether to keep your
wozmod settings. Or delete `d3d9.dll` and the `wozmod` folder from the game
folder yourself. Your game and your saves stay as they were.

## Settings

Open **Options**, then **Game Settings**. wozmod adds these rows:

| Row | What it does |
|---|---|
| TEXT LANGUAGE | English or Japanese. The change applies the next time you start the game. |
| VOICE LANGUAGE | English or Japanese. The change applies from the next line. |
| FIELD OF VIEW | 100% (original) to 150%. |
| FRAME RATE | 60 or 120 FPS in battle. |

In **Options**, then **Graphics**, wozmod changes these rows:

| Row | What it does |
|---|---|
| RESOLUTION | Sizes from 1280x720 up to the size of the monitor the game is on. |
| DISPLAY MODE | WINDOWED, BORDERLESS (a window without a frame that covers the monitor) or FULLSCREEN. |

wozmod keeps these settings in `wozmod/settings.ini`. Your save file does not
change.

## Troubleshooting

- **Windows says "Windows protected your PC" when you open wozmod.** wozmod
  is not signed, so Windows does not know the publisher. Click **More info**,
  then **Run anyway**.
- **The game is still in Japanese, and the new settings are missing.** The game
  did not load wozmod. Make sure that `d3d9.dll` is in the same folder as
  `OROCHI_Z.exe`. On Linux, add the launch options from
  [Extra steps on Linux](#extra-steps-on-linux).
- **`wozmod/runtime.log` says "refused: OROCHI_Z.exe is not the stock
  executable".** Your `OROCHI_Z.exe` is not the version that wozmod supports.
  If you installed a different English patch, reinstall the game. Then install
  wozmod again.
- **`wozmod/runtime.log` has another line that starts with "refused".** wozmod
  switched that part off because something did not match. If the line names a
  file in the `wozmod` folder, install wozmod again.
- **The screen stays black at start (Linux).** The game must start in its own
  folder. If you added it to Steam as a non-Steam game, set **Start In** to the
  game folder.

## Use the terminal instead

`wozmod` does the same as the setup window, in a terminal. Open a terminal in
the release folder and run `wozmod`. It asks whether to install or uninstall,
then asks for each file. It remembers your files, as the window does.

To give everything in one command:

```
wozmod install --game "<Musou Orochi Z folder>" \
  --wo1-x360 <Warriors Orochi 360 .iso> --x360 <Warriors Orochi 2 360 .iso> \
  --psp <Warriors Orochi 2 PSP .iso> \
  --x360-tool <decryption program for default.xex> \
  --psp-tool <decryption program for EBOOT.BIN>
```

- **Already decrypted files:** use `--wo1-x360-file`, `--x360-file` or
  `--psp-file` in place of `--x360-tool` or `--psp-tool`. Give one or the
  other: if you give a file and the program it stands in for, or a file your
  disc image already holds, wozmod says so and stops.
- **Update:** run the same command with the new release. To reinstall the
  same version, or go back to an older one, add `--force`.
- **Uninstall:** run `wozmod uninstall --game "<Musou Orochi Z folder>"`. Add
  `--remove-settings` to also delete your wozmod settings and log, or
  `--keep-settings` to keep them without being asked.
- **Fewer processor cores:** set `WOZMOD_JOBS`, for example `WOZMOD_JOBS=2`.
- **Version:** `wozmod --version`.

The Linux launch options in
[Extra steps on Linux](#extra-steps-on-linux) apply here too.

## Build from source

Install [uv](https://docs.astral.sh/uv/) and [ffmpeg](https://ffmpeg.org/),
and [Rust](https://rustup.rs) for the faster native build. wozmod needs
Python 3.14 or later, which uv installs if it is missing. In the repository folder:

- `uv run --extra native wozmod install ...` installs, with the options
  above, plus `--lettering` and `--dll` (the two release files, which the
  repository does not hold).
- `uv run wozmod-setup` opens the window.
- `uv run pytest -q` runs the tests.

Without `--extra native`, or with `WOZMOD_NATIVE=0`, wozmod uses its slower
Python code, and builds the same files.

To build the release zip for your system, run
`uv sync --extra native --group build`, then
`uv run python packaging/build.py --assets <folder with d3d9.dll and lettering.zip>`.
`packaging/release_assets.py` makes those two files and puts them on a draft
GitHub release for the tag. Make that draft first: pushing a `v*` tag runs
the release workflow in `.github/workflows/`, which takes the two files from
the draft, builds the zips for Windows and Linux, and then takes the two
files off the draft, so the release holds only the zips.

## Legal

See also the legal notice at the top. Game and company names identify
compatibility and the games that wozmod needs. The installer reads your
copies on your own computer and builds the English files there. Nothing from
the games leaves your computer or comes with wozmod.

The English menu lettering is original artwork: vector letters drawn for
wozmod to match the look of the official menus, which the release ships
rendered, together with the outlines of wozmod's own in-game text face. It
contains no font files or images from any game, and it is covered by the same
licence as the rest of wozmod.

wozmod is free software under the [GNU General Public License v3.0 or later](LICENSE).
