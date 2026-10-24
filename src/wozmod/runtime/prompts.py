"""The Xbox button prompts: which entry 4079 icon slots the runtime swaps for WO2 X360 art, and from which item.

The one list both sides read: the art build writes these slots into prompts-xbox.bin, and wozmod.runtime generates
the runtime's prompt_slot from the keys (enrt_data.h), so the slots it loads are the slots the build wrote.
"""

PROMPT_ENTRY = 4079  # LINKDATA1 entry of the button icons; WO2 X360 ENS 13 holds the Xbox art

# MOZ slot -> WO2 X360 ENS 13 item. Both programs load the icons through one 158-entry slot table, equal in both
# (MOZ 0x718B60, WO2 X360 0x82047CB8), with the same button-id dispatchers (prompt bar MOZ 0x591F50 / X360
# 0x822D63E0), so ENS 13 item k is the Xbox art for slot k. Slots 11 and 12 are the X360 D-pad halves, which MOZ
# repainted as its attack keycaps (icon codes F0 57 / F0 58); they take the face buttons of items 2 and 3.
XBOX_PROMPTS = {**{slot: slot for slot in range(16)}, 11: 2, 12: 3, 44: 44, 178: 178, 179: 179}
