# Changelog

All notable changes to wozmod are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and wozmod follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Each English attack shout now plays at the volume its own game gave it. Before, it kept the volume set for the
  Japanese shout it replaced, so some officers shouted well above the rest: Dodomeki's shouts are now 3 dB quieter
  and Wei Yan's 2 dB. Other officers' shouts move by up to about 3.5 dB either way, to the balance their own game
  gave them.
- An officer's strategy line in battle now sits on a strip that runs the full width of the screen, as in Warriors
  Orochi 2 on Xbox 360, so long English lines no longer run off its end and fade into the battlefield. The line also
  uses that width: all but one now read on a single row instead of leaving a word or two on a second, and it sits
  centred on the strip, whether it takes one row or two.
- Text no longer runs past the edges of the screen or gets squeezed to fit its box. Battle notices and movie captions
  stay inside the screen's safe area, and take a second row only when they are too long for it. Then the two rows are
  about the same length and each is centred, and a battle notice's bar grows to hold both instead of leaving the
  second row on its faded edge. Battle dialogue rows have a little space between them, as notices and subtitles do.
  Battle dialogue, the weapon info boxes and Story Z narration use the full width of their box. Upgrade skill
  descriptions, battle history, conditions and pre-battle panels wrap at their box instead of being squeezed narrower.
- With a controller, the X button (Square on a PlayStation pad) no longer leaves a wide gap before its word in
  on-screen prompts, such as Random on the officer select screen. It now sits as close as every other button.

## [0.3.0] - 2026-11-14

### Changed

- Big fights no longer crackle, and the game plays at the loudness of other current PC games, which is quieter than
  before. Large battles added up to as much as 12 dB past what the sound output can carry; the game now mixes with
  room to spare and brings the finished sound back up without letting it clip. Voices over loud scenes, such as
  Taigong Wang's lines during the falling rocks at Odani Castle, are clean now. The balance between sounds stays the
  same.
- Characters' lines during a battle are easier to hear over the fighting. While one speaks, music and battle sounds
  drop about 4 dB further than before, by the same amount whatever your sliders are set to, and they come back when
  the line ends. A line with no recorded voice no longer quiets the battle.
- The BGM, SE and Voice sliders lower their sound by the same 3 dB a step, from full at 15 to silent at 0. Before,
  their top half barely changed anything and the bottom fell off a cliff. A slider you had lowered now sounds quieter
  than before; at full volume nothing changes.
- Enemy and object sounds keep playing past their maximum distance, as they did on Windows XP, and nearby enemy hits,
  grunts and cries stand out of a big fight. ([#11](https://github.com/Defiance4331/wozmod/issues/11))

### Fixed

- Crashes:
  - when two players start Orochi X's Dream battle with some teams
    ([#18](https://github.com/Defiance4331/wozmod/issues/18));
  - when switching characters during a Musou above 60 FPS;
  - when a squad's path started outside the map's walkable area;
  - at the opening logos while XSplit captured the game.
- High frame rates:
  - standing in a burning area drained health faster the higher the frame rate, four times as fast at 240 FPS
    ([#20](https://github.com/Defiance4331/wozmod/issues/20));
  - the True Musou close-up and the team cut-ins lost their burning backdrop above 60 FPS
    ([#12](https://github.com/Defiance4331/wozmod/issues/12));
  - the first True Musou shout came late, or early after a short stutter, and ran into the second.
- Voices:
  - a cutscene could play with no voice at all when a line started while the game was still reading from disk, as in
    the Orochi story's He Fei scene with Gan Ning and Zhang Liao; if a line ever has to wait, `wozmod/runtime.log`
    says so;
  - about 860 English lines taken from Warriors Orochi 2 for PSP played 3 dB too loud and crackled; they now play at
    their original level.
- Speed: battles slowed down, sometimes to a few frames a second, on AMD graphics cards with Resizable BAR on, and
  battle effects no longer wait on the graphics card each frame.

## [0.2.1] - 2026-10-30

### Added

- If the game crashes, `wozmod/runtime.log` and `wozmod/runtime.crash.log` say what went wrong and where.

### Fixed

- Xbox One and Xbox Series controllers work on Windows: A confirms, B cancels, the D-pad moves the cursor and the
  Xbox button prompts show. They were taken for unknown controllers, with Y as confirm, X as cancel and no D-pad.
  Other controllers that Windows treats as Xbox controllers are recognised the same way.
  ([#9](https://github.com/Defiance4331/wozmod/issues/9), [#13](https://github.com/Defiance4331/wozmod/issues/13))
- A controller that is unplugged and plugged back in works again as soon as you press a button, whatever kind it
  is. ([#13](https://github.com/Defiance4331/wozmod/issues/13))
- A crash when a new officer joins your forces after a story battle.
  ([#10](https://github.com/Defiance4331/wozmod/issues/10))

## [0.2.0] - 2026-10-29

### Added

- Battles and cutscenes truly run at 60, 120, 144, 165 or 240 FPS, or at your monitor's refresh rate, with the
  game's normal speed, sounds and effects. Game Settings has a VSYNC switch.
- DualShock 4 and DualSense controllers work without extra software, with PlayStation-style default buttons and
  button names.
- New Xbox and PlayStation button prompts, sharp up to 4K, with stick icons in the Move List. Other controllers show
  English button names in Button Settings.
- Hundreds of Dream stage lines that neither Xbox 360 release voices play their English voice from Warriors
  Orochi 2 for PSP, and the twelve Musou Orochi Z Dream briefings are voiced in English.
- Benkei and Sanzang speak English: their select voices, battle quotes and shouts. Every officer has their own
  English shouts.
- The four Warriors Orochi ending movies have English subtitles that follow the English dub while English voices
  are on.
- wozmod can be installed alongside other mods. It checks only the parts of the game files it uses, and if another
  mod changed one of them, it stops and tells you which file or feature.
- If a game file or the game's code is not what wozmod expects, it turns itself off completely and says why in
  `wozmod/runtime.log`. After a crash, that log is kept as `wozmod/runtime.crash.log`.
- The setup window tells you plainly if your game has the 1.01 update, which wozmod does not support, and how to
  use version 1.00 instead.

### Changed

- Story text (camp talk, chapter narration, Story Z talk and Dream briefings) is drawn at one consistent size, in
  boxes sized to fit it.
- More official English wording, including stage titles, Life Gauge conditions, ten PSP scene subtitles and the
  Upgrade Skills instructions. Twin weapons have their own names, and the last 18 untranslated program messages are
  in English.
- Updates rebuild only what changed and leave unchanged files in place, so they are faster and need less free disk
  space.
- Command-line installs: the disc options are now `--wo1-x360`, `--wo2-x360` and `--wo2-psp` (and
  `--wo2-x360-file`, `--wo2-psp-file`). The discs you chose with 0.1.0 are remembered.

### Removed

- The FRAME SKIP setting: frame skip is always on, so a slow frame never slows the game down.

### Fixed

- A crash on the Ability Acquisition screen, and the same kind of problem with very long officer names and the
  allied-force effect line.
- On the Ability Acquisition screen, a condition with a time limit (defeat a number of enemies within a number of
  minutes) no longer swaps the two numbers.
- With English voices on, movie, cutscene, camp-talk and stage subtitles show the line the voice says, in time with
  it.
- Weapon names are no longer squeezed: every weapon list and weapon card is laid out again to fit inside the screen.
- Outlined text, such as battle subtitles, has an even outline, and tall letters are no longer cut off at the top.
- Controller buttons no longer stick or get lost while a stick is moving.
- A controller connected after the game starts, or one that disconnects and comes back, keeps your saved button
  settings.
- Fixes from the official 1.01 patch now apply to 1.00: the falling-rock sound that kept looping stops after three
  seconds, two menu drawing glitches are gone, and the Upgrade Skills icons sit in the right place.
- The installer no longer fails when antivirus software briefly holds a temporary file.

## [0.1.0] - 2026-10-19

First release of wozmod.

[Unreleased]: https://github.com/Defiance4331/wozmod/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Defiance4331/wozmod/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Defiance4331/wozmod/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Defiance4331/wozmod/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Defiance4331/wozmod/releases/tag/v0.1.0
