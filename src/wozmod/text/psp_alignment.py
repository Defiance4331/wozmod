"""Pair stock records with the PSP English archive."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from wozmod.formats.linkdata import archive, decoded_member, ps2_members
from wozmod.formats.scripts import caption_cues, donor_text, event_lines, mesc_blocks, parse_script, quote_text

from .contexts import PSP_EVENT_CAPTIONS, PSP_MOVIE_CAPTIONS, event_scenes, joined_cue_text, overlapping_cues, psp_line_cues
from .script_alignment import (
    BRIEFINGS, PSP_QUOTE_INSERT, PSP_QUOTES, PSP_STAGE_MEMBERS, QUOTES, paired_quote_tables, stage_joins,
)


PSP_QUOTE_SUFFIX = re.compile(rb"@SV[0-9]+_[0-9]+$")


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


def paired_psp_rows(stock_dir: Path, fns_path: Path) -> list[PspPair]:
    """Return candidate occurrences without choosing translation sources."""
    members = ps2_members(fns_path, sorted({
        PSP_QUOTES, *BRIEFINGS[2], *PSP_STAGE_MEMBERS.values(),
        *PSP_EVENT_CAPTIONS.values(), *PSP_MOVIE_CAPTIONS.values(),
    }))
    rows = []

    def add(stock: bytes, english: bytes, member: int, view: str, indices: list,
            group: str, location: list) -> None:
        text = donor_text(english)
        if text is not None and text.strip():
            rows.append(PspPair(stock, ["wo2-psp", "fns", member, view, *indices], group, location))

    linkdata2 = archive(stock_dir / "LINKDATA2")
    stages = {o: parse_script(data) for o, data in linkdata2.read(PSP_STAGE_MEMBERS).items()}
    joins = stage_joins(stages, {}, {}, {o: parse_script(decoded_member(members[m])) for o, m in PSP_STAGE_MEMBERS.items()})
    for ordinal, join in joins.items():  # the records a command shows, paired through the commands (stage_join)
        pairs = join.pairs | join.notices
        for (view, stock_index), donor_index in sorted(pairs.items(), key=lambda p: (p[0][0] == "dialog", p[0][1])):
            add(getattr(stages[ordinal], view).records[stock_index].text_bytes,
                getattr(join.donor, view).records[donor_index].text_bytes, join.entry, view, [donor_index],
                "Z Dream stage lines", ["linkdata2", ordinal, view, stock_index])

    text_entries = archive(stock_dir / "LINKDATA1").read([BRIEFINGS[0], QUOTES[0]])
    briefings = mesc_blocks(text_entries[BRIEFINGS[0]])
    for row, member in enumerate(BRIEFINGS[2], start=len(BRIEFINGS[1])):
        add(briefings[row][0], mesc_blocks(decoded_member(members[member]))[0][0], member, "mesc", [0, 0],
            "Z Dream briefings", ["linkdata1", BRIEFINGS[0], "mesc", row, 0])

    # Stock table t, slot k pairs with PSP table t, slot k (script_alignment.PSP_QUOTE_INSERT and QUOTE_SLOTS).
    paired = paired_quote_tables(text_entries[QUOTES[0]], members[PSP_QUOTES], insert=PSP_QUOTE_INSERT)
    for table, (ours, theirs) in enumerate(paired):
        group = "New officers' quotes" if table - 2 in (94, 95) else "Other quotes"
        for slot, (stock, donor) in enumerate(zip(ours, theirs, strict=False)):
            add(quote_text(stock), psp_quote_text(donor), PSP_QUOTES, "quotes", [table, slot], group,
                ["linkdata1", QUOTES[0], "quotes", table, slot])

    scenes = linkdata2.read([*PSP_EVENT_CAPTIONS, *PSP_MOVIE_CAPTIONS])
    for ordinal, member in PSP_EVENT_CAPTIONS.items():
        stock, donor = event_lines(scenes[ordinal]), caption_cues(members[member])
        for line_id, joins in psp_line_cues(event_scenes(scenes[ordinal]), donor).items():
            for joined in joins if line_id in stock else ():
                add(stock[line_id], joined_cue_text(donor, joined), member, "captions", [joined], "Event subtitles",
                    ["linkdata2", ordinal, "event", line_id])
    for ordinal, member in PSP_MOVIE_CAPTIONS.items():  # the same movie: both schedules count CUE_TICKS a second
        stock, donor = caption_cues(scenes[ordinal]), caption_cues(members[member])
        for cue, joined in enumerate(overlapping_cues(stock, donor)):
            if joined:
                add(stock[cue][2], joined_cue_text(donor, joined), member, "captions", [joined], "Movie captions",
                    ["linkdata2", ordinal, "captions", cue])
    return rows
