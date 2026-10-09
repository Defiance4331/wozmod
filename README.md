# wozmod

wozmod is an unofficial fan mod for **Musou Orochi Z**, the Japanese PC
release of Warriors Orochi Z. It translates the game into English and adds new
options.

> **Status:** wozmod is in development, and there is no download yet. This page
> describes the first release.

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
- **A longer view distance** and more soldiers on screen.
- **Xbox controller support** with the Warriors Orochi 2 controls and Xbox
  button prompts.

wozmod does not change the game's own files. It adds a file, `d3d9.dll`, and a
folder, `wozmod`, to the game folder.

## What you need

1. **Musou Orochi Z for Windows** (the Japanese release), installed. wozmod
   checks the game files first. If they are a different version, wozmod
   changes nothing.
2. **For English:** your own copies of these three games:
   - **Warriors Orochi for PC** (the English release), installed or as a disc
     image.
   - **Warriors Orochi 2 for Xbox 360** (the US release), as a disc image
     (`.iso`).
   - **Warriors Orochi 2 for PSP** (the US release), as a disc image (`.iso`).

   wozmod copies the official English text, art and voices from these games
   when you install it. We cannot include them, because they belong to Koei
   Tecmo. You need all three for English. Without them, you still get the new
   options and controller support, but the text stays in Japanese.
3. **Windows 10 or later**, or **Linux** with Steam (Proton) or Wine.

## Install

1. Download the latest release from the **Releases** page of this repository.
2. Unzip it to any folder.
3. Open `wozmod-setup`.
4. Select your Musou Orochi Z folder.
5. For English, select your Warriors Orochi folder and your two Warriors
   Orochi 2 disc images.
6. Click **Install**. The installer builds the English files on your computer.
   This step takes several minutes.

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
| RENDER DISTANCE | ON doubles the view distance and shows more soldiers. |

wozmod keeps these settings in `wozmod/settings.ini`. Your save file does not
change.

## Uninstall

Delete `d3d9.dll` and the `wozmod` folder from the game folder. Your game and
your saves stay as they were.

## Problems

- **The game is still in Japanese, and the new settings are missing.** The game
  did not load wozmod. Make sure that `d3d9.dll` is in the same folder as
  `OROCHI_Z.exe`. On Linux, add the launch options above.
- **`wozmod/runtime.log` says "refused".** Your `OROCHI_Z.exe` is not the
  version that wozmod supports. If you installed a different English patch,
  reinstall the game. Then install wozmod again.
- **The screen stays black at start (Linux).** The game must start in its own
  folder. If you added it to Steam as a non-Steam game, set **Start In** to the
  game folder.

## Legal

wozmod is an unofficial fan-made modification for Musou Orochi Z. It is not
affiliated with, endorsed by, or sponsored by Koei Tecmo. Game and company
names identify compatibility and the games that wozmod needs. This repository
holds no files from the games, and you need your own copies to use it.

wozmod is free software under the [GNU General Public License v3.0 or later](LICENSE).

Developers: read [AGENTS.md](AGENTS.md).
