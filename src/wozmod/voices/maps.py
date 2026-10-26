"""Join stock voice ids to donor records."""

from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass

from wozmod.donors import WO1_X360, WO2_X360
from wozmod.formats.audio import (
    MOZ_VOICE_IDS, PSP_BRIEFING_VOICES, PSP_CLIP_BASE, PSP_SELECT_VOICES, WO1_EVENT_VOICES, WO1_NARRATION_VOICES,
    WO1_RECORD_BASE, WO1_STAGES, WO1_SUFFIX_BASE, WO2_CLIP_BASE, WO2_EVENT_VOICES, WO2_NARRATION_VOICES,
    WO2_NEW_OFFICER_SELECT_VOICES, WO2_SELECT_VOICES, WO2_STAGES, WO2_SUFFIX_BASE, block_voice_map, quote_slots,
    slot_voice_map, stage_line_voices,
)
from wozmod.formats.scripts import Script
from wozmod.text.script_alignment import DREAM_SCRIPTS, StageJoin, _voices, stage_joins


# The story stages each mode runs, with the id base its speak commands default to and the record base of the donor
# disc whose stage scripts they pair with.
STORY = ((WO1_X360.name, WO1_STAGES, WO1_SUFFIX_BASE, WO1_RECORD_BASE),
         (WO2_X360.name, WO2_STAGES, WO2_SUFFIX_BASE, WO2_CLIP_BASE))


def stage_triggers(moz: dict[int, Script], joins: dict[int, StageJoin],
                   ) -> dict[tuple[int, int], tuple[tuple[int, ...], str, int]]:
    """Each spoken story or Dream line an Xbox 360 join pairs, by (stage ordinal, line): the voice ids its speak
    commands play (their bank flags included) beside the donor and the record of the donor line its speak command's
    counterpart speaks (stage_join), whatever words either says (the trigger: decisions ledger, text row 22)."""
    base = {WO1_X360.name: WO1_RECORD_BASE, WO2_X360.name: WO2_CLIP_BASE}
    dream = {o for o, _ in DREAM_SCRIPTS}
    found = {}
    for o, join in joins.items():
        if not any(o in stages and join.source == source for source, stages, _, _ in STORY) and o not in dream:
            continue
        for i, ids in stage_line_voices(o, moz[o]).items():
            if ids and (d := join.pairs.get(("dialog", i))) is not None:
                if suffix := join.donor.dialog.records[d].dialog_suffix:
                    found[o, i] = (ids, join.source, int(suffix[1:]) - 1 + base[join.source])
    return found


def _own_record(moz_id: int) -> tuple[str, int]:
    """The donor record that files a stock voice id's take under its number: the same suffix in the same bank."""
    if moz_id < WO2_SUFFIX_BASE:
        return WO1_X360.name, moz_id - WO1_SUFFIX_BASE + WO1_RECORD_BASE
    return WO2_X360.name, moz_id - WO2_SUFFIX_BASE + WO2_CLIP_BASE


def story_voice_maps(moz: dict[int, Script], joins: dict[int, StageJoin],
                     triggers: dict[tuple[int, int], tuple[tuple[int, ...], str, int]]) -> dict[str, dict[int, int]]:
    """Map the voice ids story lines play to donor records, per donor: an id plays its lines' trigger record
    (triggers, stage_triggers). Where the story and Dream lines that share an id want different records, the id
    keeps the record filed under its own number if one of them wants it, and is otherwise left to the Dream slots
    and the donor order (8 lines, decisions ledger row 23: each line the id cannot voice gets a slot of its own,
    voice_slots). An id no stage line speaks plays the record of the suffix the paired scripts share."""
    maps: dict[str, dict[int, int]] = {WO1_X360.name: {}, WO2_X360.name: {}}
    spoken = {i for o, script in moz.items() for ids in stage_line_voices(o, script).values() for i in ids}
    story = set()
    for source, stages, suffix_base, base in STORY:
        for o, join in joins.items():
            if o in stages and join.source == source:
                story.add(o)
                maps[source] |= {n - 1 + suffix_base: n - 1 + base for v in _voices(moz[o]) & _voices(join.donor)
                                 if (n := int(v[1:])) - 1 + suffix_base not in spoken}
    wanted: dict[int, set[tuple[str, int]]] = defaultdict(set)
    for ids, source, record in triggers.values():
        for moz_id in ids:
            wanted[moz_id].add((source, record))
    voiced = {moz_id for (o, _), (ids, _, _) in triggers.items() if o in story for moz_id in ids}
    for moz_id in voiced:
        records = wanted[moz_id]
        if len(records) == 1 or _own_record(moz_id) in records:
            source, record = next(iter(records)) if len(records) == 1 else _own_record(moz_id)
            maps[source][moz_id] = record
    return maps


def lines_apart(triggers: dict[tuple[int, int], tuple[tuple[int, ...], str, int]], wo1: dict[int, int],
                wo2: dict[int, int]) -> dict[tuple[int, int], tuple[str, int]]:
    """Each line (stage ordinal, line) whose trigger record (stage_triggers) is not the record its voice id plays in
    the dialogue maps wo1 and wo2 (WO2 X360 first, the donor order), with that trigger record: the lines a voice id
    they share with another line cannot voice (decisions ledger, text row 23)."""
    def played(moz_id: int) -> tuple[str, int] | None:
        if moz_id in wo2:
            return WO2_X360.name, wo2[moz_id]
        return (WO1_X360.name, wo1[moz_id]) if moz_id in wo1 else None

    return {line: (source, record) for line, (ids, source, record) in triggers.items()
            if any(played(moz_id) != (source, record) for moz_id in ids)}


@dataclass(frozen=True)
class VoiceSlot:
    """A stage line's own voice id past the stock range (slot), the stock ids its speak commands play (voices), and
    the donor record its trigger plays there."""

    slot: int
    voices: tuple[int, ...]
    source: str
    record: int


def voice_slots(triggers: dict[tuple[int, int], tuple[tuple[int, ...], str, int]], wo1: dict[int, int],
                wo2: dict[int, int]) -> dict[tuple[int, int], VoiceSlot]:
    """Give each line lines_apart lists a voice id of its own, from MOZ_VOICE_IDS on in (stage, line) order, that
    plays its trigger record; the runtime plays it in place of the shared id when that line is spoken (decisions
    ledger, text row 23). Its partner keeps the shared id and that id's record."""
    apart = lines_apart(triggers, wo1, wo2)
    return {line: VoiceSlot(MOZ_VOICE_IDS + k, triggers[line][0], *apart[line]) for k, line in enumerate(sorted(apart))}


def slot_records(slots: dict[tuple[int, int], VoiceSlot], source: str) -> dict[int, int]:
    """Each slot whose take comes from source, mapped to its record, as the voice maps map a stock id."""
    return {s.slot: s.record for s in slots.values() if s.source == source}


def slot_table(slots: dict[tuple[int, int], VoiceSlot]) -> bytes:
    """voice/Slots.bin as the runtime reads it (runtime/voice_slots.h): u32 row count, then per stock id each line's
    speaks play, u16 stage, u16 line, u16 that id, u16 the line's slot."""
    rows = sorted((stage, line, voice, s.slot) for (stage, line), s in slots.items() for voice in s.voices)
    return struct.pack(f"<I{4 * len(rows)}H", len(rows), *(v for row in rows for v in row))


def _joined_lines(moz: dict[int, Script], joins: dict[int, StageJoin]) -> list[tuple[int, bytes]]:
    """Each voice id a joined stage line plays (the id its speak commands' bank flags give) beside the donor line its
    speak command's counterpart speaks (stage_join): a line no command speaks plays nothing and pairs with nothing."""
    return [(moz_id, join.donor.dialog.records[join.pairs["dialog", i]].value)
            for o, join in joins.items() for i, ids in stage_line_voices(o, moz[o]).items()
            if ("dialog", i) in join.pairs for moz_id in ids]


def dialogue_voice_maps(
    moz: dict[int, Script], wo1_scripts: dict[int, Script], wo2_scripts: dict[int, Script],
    wo1_quote_tables: list[tuple[list[bytes], list[bytes]]],
    wo2_quote_tables: list[tuple[list[bytes], list[bytes]]], joins: dict[int, StageJoin] | None = None,
) -> tuple[dict[int, int], dict[int, int]]:
    """Combine story lines, fixed runs, quote slots and Dream slots; joins is stage_joins(moz, wo1_scripts,
    wo2_scripts), computed here unless given."""
    joins = stage_joins(moz, wo1_scripts, wo2_scripts) if joins is None else joins
    story = story_voice_maps(moz, joins, stage_triggers(moz, joins))
    wo1, wo2 = story[WO1_X360.name], story[WO2_X360.name]
    for block in (WO1_EVENT_VOICES, *WO1_NARRATION_VOICES):
        wo1 |= block_voice_map(block)
    for block in (WO2_EVENT_VOICES, WO2_SELECT_VOICES, *WO2_NARRATION_VOICES):
        wo2 |= block_voice_map(block)
    wo2 |= WO2_NEW_OFFICER_SELECT_VOICES
    # A WO1 X360 quote voices its slot (the same slot join as X360's), whatever its words: it plays only where the
    # X360 clip is missing or a placeholder.
    wo1 |= slot_voice_map(quote_slots(wo1_quote_tables), WO1_RECORD_BASE)
    slots = quote_slots(wo2_quote_tables)
    # A Dream line takes the take of the donor line its speak command's counterpart speaks.
    dream = {o for o, _ in DREAM_SCRIPTS}
    slots += _joined_lines(moz, {o: j for o, j in joins.items() if o in dream})
    # A Dream slot cannot replace a different story clip.
    wo2 |= {i: clip for i, clip in slot_voice_map(slots, WO2_CLIP_BASE).items()
            if wo2.get(i, clip) == clip}
    return wo1, wo2


def psp_voice_map(moz: dict[int, Script], psp_stages: dict[int, Script],
                  quote_tables: list[tuple[list[bytes], list[bytes]]]) -> dict[int, int]:
    """Map stock voice ids to WO2 PSP clips: the Z Dream stages' lines (psp_stages holds the PSP script of each, by
    stock ordinal), the quote slots both games voice by line suffix, the two new officers' select voices and the Z
    Dream briefings. The PSP voices its other quote slots with codes no bank holds, so those stay unmapped."""
    slots = _joined_lines(moz, stage_joins(moz, {}, {}, psp_stages))
    return (block_voice_map(PSP_SELECT_VOICES) | block_voice_map(PSP_BRIEFING_VOICES)
            | slot_voice_map(slots + quote_slots(quote_tables), PSP_CLIP_BASE))
