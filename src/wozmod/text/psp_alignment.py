"""Pair stock records with the PSP English archive."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from wozmod.formats.linkdata import decoded_member, moz_entries, ps2_members
from wozmod.formats.scripts import (
    Script, caption_cues, donor_text, event_lines, mesc_blocks, parse_script,
    quote_tables, quote_text,
)

from .script_alignment import BRIEFINGS, QUOTES


PSP_QUOTE_SUFFIX = re.compile(rb"@SV[0-9]+_[0-9]+$")
PSP_STAGE_MEMBERS = {ordinal: ordinal + 138 for ordinal in range(132, 144)}
PSP_EVENT_MEMBERS = {
    **{402 + index: 164 + index for index in range(6)},
    **{408 + index: 158 + index for index in range(6)},
}
PSP_MOVIE_MEMBERS = {175: 196, 176: 197}


@dataclass(frozen=True)
class PspPair:
    stock: bytes
    ref: list
    group: str
    location: list


def psp_quote_text(line: bytes) -> bytes:
    """Remove the route prefix and either quote voice suffix."""
    text = quote_text(line)
    match = PSP_QUOTE_SUFFIX.search(text)
    if match is None:
        return text
    at = 0
    while at < match.start():
        at += 2 if 0x81 <= text[at] <= 0x9F or 0xE0 <= text[at] <= 0xFC else 1
    return text[:at] if at == match.start() else text


def stage_slots(ordinal: int, stock: Script, donor: Script) -> list[tuple[str, int, int]]:
    """Pair stage slots by ordinal, omitting the two stock-only additions."""
    pairs = []
    for view in ("notices", "dialog"):
        ours = getattr(stock, view).records
        theirs = getattr(donor, view).records
        indices = list(range(len(ours)))
        if ordinal == 139 and view == "dialog":
            if (len(ours), len(theirs)) != (76, 64):
                raise ValueError("PSP stage 139 dialog counts differ from the insertion layout")
            indices = indices[:56] + indices[68:]
        elif ordinal == 142 and view == "notices":
            if (len(ours), len(theirs)) != (56, 49):
                raise ValueError("PSP stage 142 notice counts differ from the appended layout")
            indices = indices[:49]
        if len(indices) != len(theirs):
            raise ValueError(f"PSP stage {ordinal} {view} counts differ")
        for donor_index, stock_index in enumerate(indices):
            if ours[stock_index].value[:2] != theirs[donor_index].value[:2]:
                raise ValueError(f"PSP stage {ordinal} {view} control prefixes differ")
            pairs.append((view, stock_index, donor_index))
    return pairs


def event_caption_slots(
    stock: dict[int, bytes], donor: list[tuple[int, int, bytes]],
) -> list[tuple[int, int]]:
    """Pair sorted event ids to cue ordinals only when counts agree."""
    indices = sorted(index for index in stock if index != 81)
    if len(indices) != len(donor):
        return []
    return list(zip(indices, range(len(donor))))


def paired_psp_rows(stock_dir: Path, fns_path: Path) -> list[PspPair]:
    """Return candidate occurrences without choosing translation sources."""
    members = ps2_members(fns_path, sorted({
        27, *range(61, 73), *PSP_STAGE_MEMBERS.values(),
        *PSP_EVENT_MEMBERS.values(), *PSP_MOVIE_MEMBERS.values(),
    }))
    rows = []

    def add(stock: bytes, english: bytes, member: int, view: str, indices: list,
            group: str, location: list) -> None:
        text = donor_text(english)
        if text is not None and text.strip():
            rows.append(PspPair(stock, ["psp", "fns", member, view, *indices], group, location))

    stages = moz_entries(
        stock_dir / "LINKDATA2.BIN", stock_dir / "LINKDATA2.IDX", PSP_STAGE_MEMBERS,
    )
    for ordinal, member in PSP_STAGE_MEMBERS.items():
        stock, donor = parse_script(stages[ordinal]), parse_script(decoded_member(members[member]))
        for view, stock_index, donor_index in stage_slots(ordinal, stock, donor):
            ours, theirs = getattr(stock, view).records, getattr(donor, view).records
            add(ours[stock_index].text_bytes, theirs[donor_index].text_bytes, member, view,
                [donor_index], "Z Dream stage lines", ["linkdata2", ordinal, view, stock_index])

    text_entries = moz_entries(
        stock_dir / "LINKDATA1.BIN", stock_dir / "LINKDATA1.IDX", [BRIEFINGS[0], QUOTES[0]],
    )
    briefings = mesc_blocks(text_entries[BRIEFINGS[0]])
    for row in range(28, 40):
        member = 33 + row
        blocks = mesc_blocks(decoded_member(members[member]))
        if len(blocks) != 1 or len(briefings[row]) != len(blocks[0]):
            raise ValueError(f"PSP briefing {row} counts differ")
        for slot, (stock, donor) in enumerate(zip(briefings[row], blocks[0])):
            add(stock, donor, member, "mesc", [0, slot], "Z Dream briefings",
                ["linkdata1", BRIEFINGS[0], "mesc", row, slot])

    stock_quotes, donor_quotes = quote_tables(text_entries[QUOTES[0]]), quote_tables(members[27])
    if len(stock_quotes) != 109 or len(donor_quotes) != 109:
        raise ValueError("PSP quote table counts differ from the officer layout")
    for table, (ours, theirs) in enumerate(zip(stock_quotes, donor_quotes)):
        if len(ours) != len(theirs):
            raise ValueError(f"PSP quote table {table} slot counts differ")
        group = "New officers' quotes" if table - 2 in (94, 95) else "Other quotes"
        for slot, (stock, donor) in enumerate(zip(ours, theirs)):
            add(quote_text(stock), psp_quote_text(donor), 27, "quotes", [table, slot], group,
                ["linkdata1", QUOTES[0], "quotes", table, slot])

    scenes = moz_entries(
        stock_dir / "LINKDATA2.BIN", stock_dir / "LINKDATA2.IDX",
        [*PSP_EVENT_MEMBERS, *PSP_MOVIE_MEMBERS],
    )
    for ordinal, member in PSP_EVENT_MEMBERS.items():
        stock, donor = event_lines(scenes[ordinal]), caption_cues(members[member])
        for line_id, cue in event_caption_slots(stock, donor):
            add(stock[line_id], donor[cue][2], member, "captions", [[cue]], "Event subtitles",
                ["linkdata2", ordinal, "event", line_id])
    for ordinal, member in PSP_MOVIE_MEMBERS.items():
        stock, donor = caption_cues(scenes[ordinal]), caption_cues(members[member])
        if len(stock) == len(donor):
            for cue, (ours, theirs) in enumerate(zip(stock, donor)):
                add(ours[2], theirs[2], member, "captions", [[cue]], "Movie captions",
                    ["linkdata2", ordinal, "captions", cue])
    return rows
