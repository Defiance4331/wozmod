"""The pad button prompts: which entry 4079 icon slots the runtime swaps, and the pad families that have a set.

The one list both sides read: the art build writes these slots into prompts-<family>.bin, and wozmod.runtime
generates the runtime's prompt_slot from it (enrt_data.h), so the slots it loads are the slots the build wrote.
"""

PROMPT_ENTRY = 4079  # LINKDATA1 entry of the button icons

# Slots 0-15 (face buttons, D-pads, shoulders, START, BACK, right stick; 11 and 12 are the X360 D-pad halves, which
# MOZ repainted as its attack keycaps and which show the top and left face buttons), 44 (the 2P strip) and the stick
# clicks 178, 179. The order is the runtime's prompt_handle order.
PROMPT_SLOTS = (*range(16), 44, 178, 179)

# Items of our copy of entry 4079 that hold the stick-direction icons the Move List draws (LS up, down, left, right, then
# RS): stock items the game draws elsewhere, but the game never loads our copy; the runtime makes handles only for
# PROMPT_SLOTS and these, and swaps only PROMPT_SLOTS into the game's icon table.
STICK_ITEMS = tuple(range(240, 248))

# In the runtime's prompt set order after the keyboard (PAD_XBOX, PAD_PLAYSTATION)
PROMPT_FAMILIES = ("xbox", "playstation")
