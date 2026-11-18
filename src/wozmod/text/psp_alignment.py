"""Pair stock records with the PSP English archive."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from wozmod import game_files
from wozmod.formats.events import EVENT_TICKS, EventScene, event_scenes
from wozmod.formats.linkdata import decoded_member, ps2_members
from wozmod.formats.scripts import caption_cues, donor_text, event_lines, mesc_blocks, parse_script, quote_text

from .movie_captions import CUE_TICKS, overlapping_cues
from .script_alignment import (
    BRIEFINGS, PSP_QUOTE_INSERT, PSP_QUOTES, PSP_STAGE_MEMBERS, QUOTES, paired_quote_tables, stage_joins,
)


# The WO2 PSP plays the Z Dream scenes (stock events 402-413) as movies GEV_201 to GEV_212 and loads the caption
# schedule FNS member GEV - 0x2B (EBOOT loader 0x5BA64, vaddr; 0x5BB18), and its movies 30 and 31 load member
# n + 0xAA - 4 (0x5BB64, 0x5BB68), the scenes of stock schedules 175 and 176 (movie200, movie201). No number links a
# stock event to a GEV file: the scenes pair by time (every PSP cue starts where a stock line's window starts,
# tests/test_text_psp_alignment.py), and lines pair with cues by time within a scene (psp_line_cues).
PSP_EVENT_CAPTIONS = {**{402 + i: 164 + i for i in range(6)}, **{408 + i: 158 + i for i in range(6)}}
PSP_MOVIE_CAPTIONS = {175: 196, 176: 197}


def psp_line_cues(scene: EventScene, cues: list[tuple[int, int, bytes]]) -> dict[int, list[list[int]]]:
    """Each stock event line's cues in the WO2 PSP caption schedule of the same scene, by time: the PSP plays the
    scene as a movie and shows cue k from its start to its end (CUE_TICKS a second); MOZ shows the line from its
    speak to what ends it (EVENT_TICKS a second, runner 0x49D9A0). The movie is the sub-scene that speaks the lines,
    so both clocks start together; a cue joins the window it overlaps most. A line spoken twice may have two."""
    if len({w.sub for w in scene.windows}) > 1:
        raise ValueError("lines spoken in more than one sub-scene: the PSP movie's clock is not known")
    seconds = [(w.start / EVENT_TICKS, w.end / EVENT_TICKS, None) for w in scene.windows]
    found: dict[int, list[list[int]]] = {}
    for w, joined in zip(scene.windows, overlapping_cues(seconds, [(s / CUE_TICKS, e / CUE_TICKS, None)
                                                                   for s, e, _ in cues]), strict=True):
        if joined and joined not in found.setdefault(w.index, []):
            found[w.index].append(joined)
    return {index: joined for index, joined in found.items() if joined}


def joined_cue_text(cues: list[tuple[int, int, bytes]], joined: list[int]) -> bytes:
    """The words of the cues joined to one place, as the resolver reads a ["captions", joined] pointer."""
    return b" ".join(word for k in joined for word in cues[k][2].split())


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

    linkdata2 = game_files.archive(stock_dir / "LINKDATA2")
    stages = {o: parse_script(data) for o, data in linkdata2.read(PSP_STAGE_MEMBERS).items()}
    joins = stage_joins(stages, {}, {}, {o: parse_script(decoded_member(members[m])) for o, m in PSP_STAGE_MEMBERS.items()})
    for ordinal, join in joins.items():  # the records a command shows, paired through the commands (stage_join)
        pairs = join.pairs | join.notices
        for (view, stock_index), donor_index in sorted(pairs.items(), key=lambda p: (p[0][0] == "dialog", p[0][1])):
            add(getattr(stages[ordinal], view).records[stock_index].text_bytes,
                getattr(join.donor, view).records[donor_index].text_bytes, join.entry, view, [donor_index],
                "Z Dream stage lines", ["linkdata2", ordinal, view, stock_index])

    text_entries = game_files.archive(stock_dir / "LINKDATA1").read([BRIEFINGS[0], QUOTES[0]])
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
