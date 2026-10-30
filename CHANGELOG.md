# Changelog

All notable changes to wozmod are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and wozmod follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Defiance4331/wozmod/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/Defiance4331/wozmod/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Defiance4331/wozmod/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Defiance4331/wozmod/releases/tag/v0.1.0
