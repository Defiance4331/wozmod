# Menu design language

How wozmod lays out every menu, list, card and text box it changes. Each patch that moves or resizes UI follows
these rules, and each rule names the check that proves it (section 7). Values are logical pixels on the game's
1280x720 layout; harness captures are 1.5x that.

The goal is one sentence: **the game looks as if Koei had shipped it in English.** English text is longer than
Japanese, so boxes grow; when they grow, they grow the way the official art and the X360 English release would have.

## 1. Principles, in priority order

An explicit decision of the project owner outranks every rule here.

1. **Never cut, never overflow.** No text past its box, no box past the safe area (section 2), no box over another.
2. **Names are never squeezed.** A list slot is sized for the widest real string it can show, measured with the
   runtime's own metrics. Condensing stays the fallback only for free text (descriptions, hints, titles), and only
   by what is still needed after using the free room.
3. **One text size.** Every menu string draws at cell 24. X360 cells are a different unit:
   X360 20 is our 24 (`CELL_EDITS` in `tests/test_runtime_x360_layout.py`).
4. **Official first.** Frames, plates, tabs and icons are the stock art at native size. Where the X360 English
   release has the screen, its geometry is the starting point; where it does not, stock Japanese is.
5. **One rule per kind of element.** The same kind of element (a list row, a tab, a card) has the same geometry on
   every screen. A screen does not get its own exception.
6. **Derived, not tuned.** Every grown width is computed from the content it holds and the tokens below. A
   measured offset that only makes one screen look right is a workaround (AGENTS.md, Root causes).

## 2. Canvas and safe area

| Token | Value | Source |
|---|---|---|
| Layout | 1280 x 720 | the game's logical screen |
| Safe area | x 64..1216, y 36..684 | 5% on each side: the X360's own caption lane (1152 wide at 64) |

Every panel's frame box (the x and width the game draws the frame with, as the harness logs it) stays inside
the safe area; the stock frame art may draw its own border shadow past that box. Stock full-width art keeps its stock
place: bars (the help bar strip, the title strip, captions) and full-screen backdrops.

## 3. Spacing

The X360 menus space on a 7 px unit. Use these steps; anything else needs a stock or X360 source.

| Token | Value | Use |
|---|---|---|
| `u` | 7 | the unit |
| Row gap | 4 | between row plates (stock, from plate 38 on pitch 42) |
| Panel padding | 8 or 14 | frame edge to row plate: each frame kind keeps its X360 value |
| Trailing gap | 14 (2u) at least | end of a name lane to the next element (skill squares, counts) |
| Tab overlap | 16 | a header tab's bottom sits 16 above its panel's frame top (stock) |
| Panel gap | 28 (4u) | between side-by-side panels on one page |
| Row pitch | 42 (6u) for rows with icons (skills, weapons); 41 for name-only lists | X360 |

## 4. Type

| Token | Value |
|---|---|
| Cell | 24 for every menu string (names, counts, tabs, hints, help bar, descriptions) |
| Text inset in a plate | 10 from the plate's left edge to the name's ink (within 1 px); trailing numbers end 10 from its right edge |
| Vertical | ink centred in the plate, within 1 px |
| Free-text boxes | the stock inset of that box kind (scaled with the box when the box grows) |

## 5. Elements

### List rows

A row is `[icon column][name lane][trailing gap][trailing element][inset]`. The name lane is the widest string of
the set at cell 24, so no name condenses:

- **Weapon row** (0x595050). Stock plate 350: check mark, thumbnail column +38..+94, name at +101, skill squares
  at +280. The lane holds the widest English weapon name, 254 px (weapon id 345, measured with
  `latin_font.width` over all 384 names). Squares then start at +101 + 254 + 14 = 369 or later, so the
  row grows by the smallest multiple of `u` that reaches it: **91**. Plate 441. Every frame drawn around the row
  grows by the same 91, and every list keeps the side the stock page holds (the existing shared rule,
  `WEAPON_ROW_GROWTH`).
- **Skill row, officer row**: name lane = widest skill or officer name at cell 24; inset 10; trailing count ends 10
  from the right.

### Panels on a page

1. Each panel's width comes from its content (rows above, padding of its frame kind).
2. Place the panels at the reference x (X360, else stock).
3. If the page then breaks section 2, lay it out again: the panels keep their order and vertical place, sit 28
   apart, and the group is centred in the safe area. Each panel still slides in from where it did, so only the
   travel of each slide changes.
4. Panels stacked in one column share both edges: a description box above a weapon card is as wide as the card.
5. A panel that stays on screen across the steps of one flow (the weapon card from picking a weapon to choosing
   its upgrade) keeps one place on every step. The step with the widest group sets it by steps 2 and 3; on the
   other steps the remaining panels sit 28 from it, and the group may sit off centre.

### Header tabs

A tab (the art behind a panel's label) sits 16 above its panel and keeps its kind's stock x offset from the
panel's left edge. Its label keeps its natural width and condenses only as far as its tab needs.

### Hint line and help bar

The hint line under the page title ends at x 1183 (stock) and condenses into its strip; it is never cut. The help
bar's text ends at x 1184 and grows to the left.

Its quad ends at 1183 and its ink at 1175 (`camp_help_position`). Its strip runs from a trailing gap (14) past the
title's ink to that 1175. A hint wider than its strip carries the strip's width as its row's `room` in
`data/art/menu_textures.json`, and the art build condenses it to that width, both the stock-size art and the large
art. The quad the runtime pushes then holds the condensed row (`HELP_QUAD_WIDTH`, `camp_help_position` row).

### Cards

The weapon card (0x595310 and the pages that copy it) is built from its content:

- **Width 645**: padding 14, the attribute grid (4 cells of 149 on a 156 step, the widest label and a two-digit
  level with room to spare), padding 14.
- **Thumbnail** at the left padding. **One text column** 14 to its right: the name, the Base Attack label and the
  first skill tag share that left edge. The tag row (3 tags of 145 on a 152 step) and the grid end on the same
  right edge.
- **Vertical rhythm**: the name's ink top sits on the thumbnail's top edge, the tag plates end on its bottom edge,
  and the Base Attack line sits midway between them.
- **Numbers after words**: the +N suffix and the Base Attack value start 14 after the ink before them, whatever
  their digits (set back by each leading digit's measured ink offset). Levels in the grid are right-aligned.

The card obeys the panel rules like any other panel: on its pages the list and the card are laid out as one group.

### Motion

Panels slide in from their stock off-screen start. A page that is laid out again (Panels on a page, step 3) keeps
those starts and changes only the slide travel.

## 6. Rule breaks fixed by the 2026-10-27 relayout

The 2026-10-27 survey of the English build against stock Japanese and the X360 code found these breaks. The relayout
that followed it (commit b64efcb) applied the After column; `data/checks/layout.toml` checks the result in the game.

| Rule | Screen | Before | After |
|---|---|---|---|
| Safe area | Upgrade Skills, officer page | weapon list 831..1337 (57 past the edge) | page laid out again, inside 64..1216 |
| Safe area | Upgrade Skills, weapon page | Source Weapon card 636..1286 | inside the safe area |
| Safe area | Weapon pick, Fusion weapons, Attributes | card 627..1277 (3 from the edge) | inside the safe area |
| Name lane | every weapon list | row grown 128: lane 303 for a widest name of 254 | row grown 91 |
| Column edges | Upgrade Skills weapon page, Attributes | description box 472 above a 650 card | box as wide as the card |
| Text inset | officer lists on the three Weapon pages | names 7 in | 10 in |
| Text inset | weapon rows | 11 in | 10 in |
| Never cut | Upgrade Skills, officer page | hint line cut at its strip's edge | condensed into the strip |
| Safe area | weapon card on the discard and pre-battle pages | right edge 15 to 25 from the screen edge | inside the safe area |
| Safe area | pre-battle officer list | left edge 56 | inside the safe area |
| Safe area | Story Z still text box | 40..1240 (grown from stock 192..1088) | kept: it is the X360's own full-width bar (40 from each edge) |
| One rule per kind | upgrade pages vs other weapon lists | icon at +52 vs +46 (frame padding 14 vs 8) | the same row, each frame kind's padding |

## 7. Checks

Every rule above is enforced in the game by `data/checks/layout.toml` and offline by tests. Kinds that exist:
`text_within_box`, `quad_within_box`, `box_within_safe_area` (every logged box on the screen inside x 64..1216,
but the stock full-screen backdrops in `SAFE_AREA_FULL_WIDTH`), `text_centred_in_box`, `text_cell`, `text_inset` (a
list's names start 10 inside their plates). Kinds to add with the rollout:

- `panel_gap`: side-by-side frames on a page sit 28 apart.
- A unit test that each name lane (weapon, skill, officer) holds the widest string of its set at cell 24, and that
  each grown width is the smallest multiple of `u` that does.

A new screen is done when its rows in `layout.toml` pass and its contact sheet has been looked at.
