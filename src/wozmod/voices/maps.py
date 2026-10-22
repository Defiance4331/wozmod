"""Join stock voice ids to donor records."""

from __future__ import annotations

from wozmod.formats.audio import (
    PSP_CLIP_BASE, PSP_SELECT_VOICES, WO1_EVENT_VOICES, WO1_NARRATION_VOICES, WO1_RECORD_BASE, WO1_STAGES,
    WO1_SUFFIX_BASE, WO2_CLIP_BASE, WO2_EVENT_VOICES, WO2_NARRATION_VOICES,
    WO2_SELECT_VOICES, WO2_STAGES, WO2_SUFFIX_BASE, block_voice_map, slot_voice_map,
)
from wozmod.formats.scripts import Script, quote_text
from wozmod.text.psp_alignment import stage_slots
from wozmod.text.script_alignment import DREAM_SCRIPTS, _voices, pair_scripts


def _voice_map(moz: dict[int, Script], donors: dict[int, Script], stages: range,
               suffix_base: int, record_base: int) -> dict[int, int]:
    pairs = pair_scripts({o: s for o, s in moz.items() if o in stages}, list(donors.values()))
    return {int(v[1:]) - 1 + suffix_base: int(v[1:]) - 1 + record_base
            for o, donor in pairs.items() for v in _voices(moz[o]) & _voices(donor)}


def wo1_voice_map(moz: dict[int, Script], wo1: dict[int, Script]) -> dict[int, int]:
    """Map WO1 story suffixes to WO1 X360 records."""
    return _voice_map(moz, wo1, WO1_STAGES, WO1_SUFFIX_BASE, WO1_RECORD_BASE)


def wo2_voice_map(moz: dict[int, Script], wo2: dict[int, Script]) -> dict[int, int]:
    """Map WO2 story suffixes to X360 clips."""
    return _voice_map(moz, wo2, WO2_STAGES, WO2_SUFFIX_BASE, WO2_CLIP_BASE)


def dialogue_voice_maps(
    moz: dict[int, Script], wo1_scripts: dict[int, Script], wo2_scripts: dict[int, Script],
    wo1_quote_tables: list[tuple[list[bytes], list[bytes]]],
    wo2_quote_tables: list[tuple[list[bytes], list[bytes]]],
) -> tuple[dict[int, int], dict[int, int]]:
    """Combine story, fixed runs, matching quote fallbacks and Dream slots."""
    wo1 = wo1_voice_map(moz, wo1_scripts)
    for block in (WO1_EVENT_VOICES, *WO1_NARRATION_VOICES):
        wo1 |= block_voice_map(block)
    wo2 = wo2_voice_map(moz, wo2_scripts)
    for block in (WO2_EVENT_VOICES, WO2_SELECT_VOICES, *WO2_NARRATION_VOICES):
        wo2 |= block_voice_map(block)
    wo1 |= slot_voice_map([
        (ours, [a if b and quote_text(a) == quote_text(b) else b"" for a, b in zip(w1, w2, strict=False)])
        for (ours, w1), (_, w2) in zip(wo1_quote_tables, wo2_quote_tables, strict=False)
    ], WO1_RECORD_BASE)
    slots = [([r.value for r in moz[o].dialog.records],
              [r.value for r in wo2_scripts[m].dialog.records])
             for o, m in DREAM_SCRIPTS] + wo2_quote_tables
    # A Dream slot cannot replace a different story clip.
    wo2 |= {i: clip for i, clip in slot_voice_map(slots, WO2_CLIP_BASE).items()
            if wo2.get(i, clip) == clip}
    return wo1, wo2


def psp_voice_map(moz: dict[int, Script], psp_stages: dict[int, Script],
                  quote_tables: list[tuple[list[bytes], list[bytes]]]) -> dict[int, int]:
    """Map stock voice ids to WO2 PSP clips: the Z Dream stages' lines (psp_stages holds the PSP script of each, by
    stock ordinal), the quote slots both games voice by line suffix, and the two new officers' select voices. The PSP
    voices its other quote slots with codes no bank holds, so those stay unmapped."""
    slots = []
    for ordinal, donor in psp_stages.items():
        paired = [(moz[ordinal].dialog.records[ours].value, donor.dialog.records[theirs].value)
                  for view, ours, theirs in stage_slots(ordinal, moz[ordinal], donor) if view == "dialog"]
        slots.append(([a for a, _ in paired], [b for _, b in paired]))
    return block_voice_map(PSP_SELECT_VOICES) | slot_voice_map(slots + quote_tables, PSP_CLIP_BASE)
