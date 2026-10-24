"""Join stock voice ids to donor records."""

from __future__ import annotations

from collections import Counter
from typing import Callable

from wozmod.donors import WO1_X360, WO2_X360
from wozmod.formats.audio import (
    PSP_CLIP_BASE, PSP_SELECT_VOICES, WO1_EVENT_VOICES, WO1_NARRATION_VOICES, WO1_RECORD_BASE, WO1_STAGES,
    WO1_SUFFIX_BASE, WO2_CLIP_BASE, WO2_EVENT_VOICES, WO2_NARRATION_VOICES,
    WO2_SELECT_VOICES, WO2_STAGES, WO2_SUFFIX_BASE, block_voice_map, quote_slots, slot_voice_map,
    stage_line_voices,
)
from wozmod.formats.scripts import Script, quote_text
from wozmod.text.psp_alignment import stage_slots
from wozmod.text.script_alignment import DREAM_SCRIPTS, X360_ENS, _voices, pair_scripts
from wozmod.text.stock import stock_key


# A slot keys the voice id its stock line plays (a flagged line plays the other bank's id), and a line no speak command
# speaks keys the id of its stage's default bank, which another line with its suffix may play: Dream stage 51 speaks
# lines 57 and 58, and the donor takes of their suffixes sit in the slots of its unspoken lines 26 and 27.
UNSPOKEN = 0


def shown_lines(moz: dict[int, Script], rows: dict[str, dict]) -> dict[bytes, list]:
    """The donor pointer of each stock spoken line (by its whole value) whose subtitle row is its own: the row the
    runtime finds for it (the line with its @NNNN suffix first, then its text), shown by no other spoken line."""
    keyed = {}
    for script in moz.values():
        for line in script.dialog.records:
            if line.dialog_suffix:
                keyed[line.value] = next((found[0] for raw in (line.value[2:], line.text_bytes)
                                          if (found := stock_key(raw)) and found[0] in rows), None)
    shown = Counter(keyed.values())
    return {value: rows[key]["ref"] for value, key in keyed.items()
            if key and shown[key] == 1 and "ref" in rows[key]}


def _voice_map(moz: dict[int, Script], donors: dict[int, Script], stages: range, suffix_base: int, record_base: int,
               shown: dict[bytes, list], source: str, ens: Callable[[int], int]) -> dict[int, int]:
    """Join paired stage scripts by voice suffix. A line whose own row shows another line of the paired donor script
    with other words takes that line's voice instead (WO1 X360 stage 107 has two of one officer's sentences in the
    other order), so the voice says the subtitle; where the words are the same, the line keeps its own take."""
    found = {}
    ordinal = {id(script): m for m, script in donors.items()}
    for o, donor in pair_scripts({o: s for o, s in moz.items() if o in stages}, list(donors.values())).items():
        found |= {int(v[1:]) - 1 + suffix_base: int(v[1:]) - 1 + record_base for v in _voices(moz[o]) & _voices(donor)}
        own = {line.dialog_suffix: line.text_bytes for line in donor.dialog.records if line.dialog_suffix}
        for line in moz[o].dialog.records:
            ref = shown.get(line.value) if line.dialog_suffix else None
            if not (ref and len(ref) == 5 and ref[:4] == [source, "ens", ens(ordinal[id(donor)]), "dialog"]):
                continue
            theirs = donor.dialog.records[ref[4]]
            if theirs.dialog_suffix and theirs.text_bytes != own.get(line.dialog_suffix):
                found[int(line.dialog_suffix[1:]) - 1 + suffix_base] = int(theirs.dialog_suffix[1:]) - 1 + record_base
    return found


def wo1_voice_map(moz: dict[int, Script], wo1: dict[int, Script], shown: dict[bytes, list] | None = None,
                  ) -> dict[int, int]:
    """Map WO1 story suffixes to WO1 X360 records (wo1 by ENS ordinal); shown is shown_lines."""
    return _voice_map(moz, wo1, WO1_STAGES, WO1_SUFFIX_BASE, WO1_RECORD_BASE, shown or {}, WO1_X360.name, lambda m: m)


def wo2_voice_map(moz: dict[int, Script], wo2: dict[int, Script], shown: dict[bytes, list] | None = None,
                  ) -> dict[int, int]:
    """Map WO2 story suffixes to X360 clips (wo2 by PS2 member id); shown is shown_lines."""
    return _voice_map(moz, wo2, WO2_STAGES, WO2_SUFFIX_BASE, WO2_CLIP_BASE, shown or {}, WO2_X360.name,
                      X360_ENS.__getitem__)


def dialogue_voice_maps(
    moz: dict[int, Script], wo1_scripts: dict[int, Script], wo2_scripts: dict[int, Script],
    wo1_quote_tables: list[tuple[list[bytes], list[bytes]]],
    wo2_quote_tables: list[tuple[list[bytes], list[bytes]]], shown: dict[bytes, list] | None = None,
) -> tuple[dict[int, int], dict[int, int]]:
    """Combine story, fixed runs, matching quote fallbacks and Dream slots; shown is shown_lines."""
    wo1 = wo1_voice_map(moz, wo1_scripts, shown)
    for block in (WO1_EVENT_VOICES, *WO1_NARRATION_VOICES):
        wo1 |= block_voice_map(block)
    wo2 = wo2_voice_map(moz, wo2_scripts, shown)
    for block in (WO2_EVENT_VOICES, WO2_SELECT_VOICES, *WO2_NARRATION_VOICES):
        wo2 |= block_voice_map(block)
    wo1 |= slot_voice_map(quote_slots([
        (ours, [a if b and quote_text(a) == quote_text(b) else b"" for a, b in zip(w1, w2, strict=False)])
        for (ours, w1), (_, w2) in zip(wo1_quote_tables, wo2_quote_tables, strict=False)
    ]), WO1_RECORD_BASE)
    slots = quote_slots(wo2_quote_tables)
    for o, m in DREAM_SCRIPTS:
        voices, theirs = stage_line_voices(o, moz[o], UNSPOKEN), wo2_scripts[m].dialog.records
        slots += [(moz_id, theirs[i].value) for i, ids in voices.items() if i < len(theirs) for moz_id in ids]
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
        voices = stage_line_voices(ordinal, moz[ordinal], UNSPOKEN)
        slots += [(moz_id, donor.dialog.records[theirs].value)
                  for view, ours, theirs in stage_slots(ordinal, moz[ordinal], donor) if view == "dialog"
                  for moz_id in voices.get(ours, ())]
    return block_voice_map(PSP_SELECT_VOICES) | slot_voice_map(slots + quote_slots(quote_tables), PSP_CLIP_BASE)
