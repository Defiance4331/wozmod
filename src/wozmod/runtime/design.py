"""Tokens of docs/design-language.md, and the attribute cell built from them: the runtime edits draw the cell at this
size (runtime_edits) and the art build widens the cell's plates to it (data/art/menu_textures.json names
ATTRIBUTE_CELL), so both read one number. A small module of its own: the art step's key covers every module the
art build imports, and runtime_edits changes far more often than these sizes."""

from __future__ import annotations

SAFE_LEFT, SAFE_RIGHT = 64, 1216  # the safe area's x range on the 1280 layout
SPACING_UNIT = 7
TRAILING_GAP = 2 * SPACING_UNIT  # a name lane's end to the next element in its row
TEXT_INSET = 10  # a plate's left edge to the name drawn on it

# An attribute cell is [unit][label art][unit][two level digits][unit] for the widest label (4079.89, whose art is
# ATTRIBUTE_QUAD_MINIMUM's widest in wozmod.art.vector) and a two-digit level. One painter draws it (0x591810).
WIDEST_ATTRIBUTE_LABEL = 92  # 4079.89's quad
LEVEL_DIGIT = 18  # the level's digit sprite 4079.78, as 0x591810 draws it
ATTRIBUTE_LABEL_X = SPACING_UNIT  # in the cell
ATTRIBUTE_CELL = ATTRIBUTE_LABEL_X + WIDEST_ATTRIBUTE_LABEL + SPACING_UNIT + 2 * LEVEL_DIGIT + SPACING_UNIT
ATTRIBUTE_STEP = ATTRIBUTE_CELL + SPACING_UNIT
LEVEL_ONES_X = ATTRIBUTE_CELL - SPACING_UNIT - LEVEL_DIGIT  # right-aligned: the ones digit ends a unit in
LEVEL_TENS_X = LEVEL_ONES_X - LEVEL_DIGIT
