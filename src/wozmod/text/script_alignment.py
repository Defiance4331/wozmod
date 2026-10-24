"""Pair stock text with donor records using the migration baseline rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Callable

from wozmod.donors import WO1_X360, WO2_X360
from wozmod.formats.linkdata import (
    archive, decoded_member,
)
from wozmod.formats.scripts import (
    LEAD, Script, ScriptError, caption_cues, donor_text, event_captions,
    event_lines, mesc_blocks, parse_script, quote_tables, quote_text, speak_flags,
)

# WO1 X360 ENS ordinals, as are the other WO1 X360 entries below. Its loader 0x822A28C0 reads ENS k as archive id 0xA53 + k;
# 0x8219D948 loads the stage script ENS 88 + stage id.
WO1_SCRIPTS = range(88, 148)


WO2_SCRIPTS = range(2796, 2864)


CAPTIONS = range(146, 171)


CAPTION_MEMBER = 2793


DREAM_SCRIPTS = tuple(zip(range(40, 68), range(2836, 2864), strict=True))


MIN_SHARED_VOICES = 0.9


# WO1 X360 loads its still pages ENS 6 (0x8226F09C) and camp talk ENS 18 (0x822912DC).
NARRATION = ((4071, WO2_X360.name, 60), (4073, WO1_X360.name, 6), (4103, WO2_X360.name, 59), (4105, WO1_X360.name, 18))


BRIEFINGS = (4190, range(101, 129))


# A WO1 event slot s loads the entry 0x82293F60 gives it, and MOZ slot s + 68 (0x49A9B0) loads the stock one.
EVENTS = ((range(177, 239), WO2_X360.name, 2692), (range(245, 283), WO1_X360.name, -96),
          (range(283, 384), WO1_X360.name, -18), (range(401, 402), WO1_X360.name, -35))


# Every WO1 X360 event file: 0x82293F60 gives each slot one of ENS 149 to 366. Slot 0x25 loads ENS 187 + b, b the
# player's officer (0 to 78), a variant per officer of one scene; MOZ plays one fixed file there (stock 283) that
# holds lines of several variants.
WO1_EVENT_ENTRIES = range(149, 367)


def donor_event_entries(source: str) -> list[int]:
    """Every event file of a donor (WO1 X360 ENS ordinals, WO2 X360 PS2 member ids), the ones EVENTS pairs first."""
    paired = sorted({o + shift for ordinals, name, shift in EVENTS if name == source for o in ordinals})
    return paired + [e for e in WO1_EVENT_ENTRIES if e not in paired] if source == WO1_X360.name else paired


QUOTES = (4084, 21, 45)


# Stock officer tables a donor lacks, as (first stock officer, count). The quote table index is the officer's
# id in all three games (MOZ 0x4F6317, WO2 X360 0x821BE828, WO1 X360 0x821A2208). X360 lacks MOZ officers 94 and 95;
# WO1 has officers 0-78 then its generic officers, which are MOZ 96-104, so it lacks MOZ 79-95.
QUOTE_INSERT = (94, 2)
WO1_QUOTE_INSERT = (79, 17)


X360_ENS = {45: 17, 59: 32, 60: 8, **{m: m - 26 for m in range(101, 129)},
            **{m: m - 2693 for m in range(2796, 2969)}}


def _parse(data: bytes) -> Script | None:
    try:
        return replace(parse_script(data), speaks=speak_flags(data))
    except ScriptError:
        return None


def moz_scripts(stem: Path) -> dict[int, Script]:
    """Read every single-member four-section stock script."""
    entries = archive(stem)
    single = entries.read(o for o in entries if entries.parts(o) == 1)
    return {o: parsed for o, data in single.items() if data[:4] == b"\x04\0\0\0" and (parsed := _parse(data))}


def wo1_scripts(stem: Path) -> dict[int, Script]:
    """Read the WO1 X360 USA stage scripts."""
    return {o: parse_script(d, ">") for o, d in archive(stem).read(WO1_SCRIPTS).items()}


@dataclass(frozen=True)
class Wo2Donor:
    """A member reader keyed by PS2 ids, with word order and row source."""

    members: Callable[[list[int]], dict[int, bytes]]
    order: str
    source: str

    def scripts(self) -> dict[int, Script]:
        return {m: parse_script(d, self.order) for m, d in self.members(list(WO2_SCRIPTS)).items()}


def x360_members(ens: Path, members) -> dict[int, bytes]:
    """Read X360 archive entries using the matching PS2 member ids."""
    found = archive(ens).read(sorted({X360_ENS[m] for m in members}))
    return {m: found[X360_ENS[m]] for m in members}


def wo2_x360(ens: Path) -> Wo2Donor:
    return Wo2Donor(partial(x360_members, ens), ">", "wo2-x360")


def _voices(entry: Script) -> set[bytes]:
    return {r.dialog_suffix for r in entry.dialog.records if r.dialog_suffix}


def pair_scripts(moz: dict[int, Script], donors: list[Script]) -> dict[int, Script]:
    """Pair stock scripts with donors sharing at least 90% of the voice union."""
    pairs = {}
    donor_voices = [(_voices(d), d) for d in donors]
    for ordinal, entry in moz.items():
        voices = _voices(entry)
        if not voices:
            continue
        best = max(donor_voices, key=lambda v: len(voices & v[0]) / len(voices | v[0]))
        if len(voices & best[0]) / len(voices | best[0]) >= MIN_SHARED_VOICES:
            pairs[ordinal] = best[1]
    return pairs


def record_pairs(moz: Script, donor: Script) -> list[tuple[bytes, str]]:
    """Pair notices by ordinal and spoken lines by unique voice suffix."""
    pairs = []
    if len(moz.notices.records) >= len(donor.notices.records):
        pairs += [(m.text_bytes, d.text_bytes)
                  for m, d in zip(moz.notices.records, donor.notices.records, strict=False)]
    by_voice: dict[bytes, list[bytes]] = {}
    for record in donor.dialog.records:
        by_voice.setdefault(record.dialog_suffix, []).append(record.text_bytes)
    for record in moz.dialog.records:
        english = by_voice.get(record.dialog_suffix) if record.dialog_suffix else None
        if english and len(english) == 1:
            pairs.append((record.text_bytes, english[0]))
    return _decoded(pairs)


def prefix_pairs(moz: Script, donor: Script) -> list[tuple[bytes, str]]:
    """Pair both tables by ordinal over the donor table length."""
    return _decoded((m.text_bytes, d.text_bytes)
                    for table in ("notices", "dialog")
                    for m, d in zip(getattr(moz, table).records, getattr(donor, table).records, strict=False))


def _decoded(pairs) -> list[tuple[bytes, str]]:
    """Keep pairs with nonblank stock text and valid nonblank donor English."""
    return [(jp, text) for jp, en in pairs
            if jp.strip(b" \n\x81\x40") and en and (text := donor_text(en)) and text.strip()]


def narration_pairs(moz: bytes, donor: bytes) -> list[tuple[bytes, str]]:
    """Pair narration positions inside equal-length blocks."""
    return _decoded(pair for a, b in zip(mesc_blocks(moz), mesc_blocks(donor), strict=False)
                    if len(a) == len(b) for pair in zip(a, b, strict=True))


def donor_event_lines(events, order: str) -> dict[int, bytes]:
    """Donor event subtitles by line index over all of the donor's event files. An event line plays voice record
    base + index in whichever file shows it (WO1 X360 0x821888F0, WO2 X360 0x8219F8D8), so the index names one line
    game-wide; an index carrying two texts is dropped."""
    lines: dict[int, bytes | None] = {}
    for data in events:
        for index, text in event_lines(data, order).items():
            lines[index] = text if lines.get(index, text) == text else None
    return {i: t for i, t in lines.items() if t is not None}


def event_pairs(moz: bytes, english: dict[int, bytes]) -> list[tuple[bytes, str]]:
    """Pair event subtitles with the donor line of the same index (donor_event_lines), the line its voice plays.
    A stock line without Japanese is no text (MOZ leaves a 0x08 byte at index 81 of its WO1 events)."""
    return _decoded((t, english[i]) for i, t in event_lines(moz).items() if i in english and re.search(LEAD, t))


def event_caption_pairs(moz: bytes, donor: bytes, order: str = "<") -> list[tuple[bytes, str]]:
    """Pair the place captions of a stock event and its donor event by sub-scene and order inside it."""
    english = {(scene, ordinal): text for scene, ordinal, _, text in event_captions(donor, order)}
    return _decoded((text, english[scene, ordinal]) for scene, ordinal, _, text in event_captions(moz)
                    if (scene, ordinal) in english)


def caption_pairs(moz: bytes, donor: bytes) -> list[tuple[bytes, bytes]]:
    """Join each donor cue to the stock cue with the greatest positive overlap."""
    ours, joined = caption_cues(moz), {}
    for start, end, text in caption_cues(donor):
        overlap, at = max((min(end, e) - max(start, s), i) for i, (s, e, _) in enumerate(ours))
        if overlap > 0 and text.strip():
            joined.setdefault(at, []).extend(text.split())
    return [(ours[at][2], b" ".join(words)) for at, words in joined.items()]


def paired_quote_tables(moz: bytes, donor: bytes, order: str = "<", insert: tuple[int, int] = QUOTE_INSERT,
                        ) -> list[tuple[list[bytes], list[bytes]]]:
    """Pair format and officer tables by officer id, leaving the stock officers the donor lacks unpaired."""
    ours, theirs = quote_tables(moz), quote_tables(donor, order)
    at, count = insert
    theirs[2 + at:2 + at] = [[]] * count
    return list(zip(ours, theirs, strict=False))


def quote_pairs(moz: bytes, donor: bytes, officers: bool = True, order: str = "<",
                insert: tuple[int, int] = QUOTE_INSERT) -> list[tuple[bytes, str]]:
    """Pair notice formats by ordinal and optionally officer quotes by slot."""
    tables = paired_quote_tables(moz, donor, order, insert)
    tables = tables if officers else tables[:2]
    return _decoded((quote_text(m), quote_text(d)) for a, b in tables for m, d in zip(a, b, strict=False) if m and d)


def official_pairs(stock: Path, wo1: Path, wo2_donor: Wo2Donor) -> dict[bytes, tuple[str, str]]:
    """Re-derive stock bytes to (English, source), preserving baseline votes and suffixes."""
    linkdata1, linkdata2 = archive(stock / "LINKDATA1"), archive(stock / "LINKDATA2")
    moz = moz_scripts(stock / "LINKDATA2")
    wo1_by_ordinal, wo2 = wo1_scripts(wo1), wo2_donor.scripts()
    wo1_ids = {id(d) for d in wo1_by_ordinal.values()}
    found = [(pairs, WO1_X360.name if id(d) in wo1_ids else WO2_X360.name) for pairs, d in (
        (record_pairs(moz[o], d), d)
        for o, d in pair_scripts(moz, [*wo1_by_ordinal.values(), *wo2.values()]).items())]
    found += [(prefix_pairs(moz[o], wo2[m]), WO2_X360.name) for o, m in DREAM_SCRIPTS]
    events = [(o, source, o + shift) for ordinals, source, shift in EVENTS for o in ordinals]
    briefing_entry, briefing_members = BRIEFINGS
    quotes_entry, wo1_quotes, wo2_quotes = QUOTES
    wanted = {WO1_X360.name: {wo1_quotes}, WO2_X360.name: {*briefing_members, wo2_quotes}}
    for _, source, entry in NARRATION:
        wanted[source].add(entry)
    for source in wanted:
        wanted[source].update(donor_event_entries(source))
    donor = {(WO1_X360.name, k): v for k, v in archive(wo1).read(sorted(wanted[WO1_X360.name])).items()}
    donor |= {(WO2_X360.name, k): v for k, v in wo2_donor.members(sorted(wanted[WO2_X360.name])).items()}
    narration = linkdata1.read([*(n for n, _, _ in NARRATION), briefing_entry, quotes_entry])
    found += [(narration_pairs(narration[n], donor[s, d]), s) for n, s, d in NARRATION]
                                                                                             
    found += [(quote_pairs(narration[quotes_entry], donor[WO1_X360.name, wo1_quotes], officers=False, order=">",
                           insert=WO1_QUOTE_INSERT), WO1_X360.name),
              (quote_pairs(narration[quotes_entry], donor[WO2_X360.name, wo2_quotes], order=wo2_donor.order), WO2_X360.name)]
    found.append((narration_pairs(narration[briefing_entry], b"".join(
        decoded_member(donor[WO2_X360.name, m]) for m in briefing_members)), WO2_X360.name))
    scenes = linkdata2.read([o for o, _, _ in events])
    lines = {s: donor_event_lines((donor[s, e] for e in donor_event_entries(s)), wo2_donor.order if s == WO2_X360.name
                                  else ">") for s in wanted}
    found += [(event_pairs(scenes[o], lines[s]), s) for o, s, _ in events]
    # WO1 X360 holds German, not English, at many of its caption sites, so only the WO2 X360 captions pair.
    found += [(event_caption_pairs(scenes[o], donor[s, d], wo2_donor.order), s) for o, s, d in events if s == WO2_X360.name]
    movies = linkdata2.read(CAPTIONS)
    captions = wo2_donor.members([o + CAPTION_MEMBER for o in CAPTIONS])
    # A movie cue takes its English by its place (text.movie_captions), so a movie caption votes only for a string
    # no other site shows: the row of a shared string serves the other sites.
    elsewhere = {japanese for pairs, _ in found for japanese, _ in pairs}
    found += [([pair for pair in _decoded(caption_pairs(movies[o], captions[o + CAPTION_MEMBER]))
                if pair[0] not in elsewhere], WO2_X360.name) for o in CAPTIONS]
    votes: dict[bytes, dict[str, list]] = {}
    for pairs, source in found:
        for japanese, english in pairs:
            vote = votes.setdefault(japanese, {}).setdefault(english, [0, set()])
            vote[0] += 1
            vote[1].add(source)
    official = {japanese: (english, WO1_X360.name if WO1_X360.name in sources else wo2_donor.source)
                for japanese, counts in votes.items()
                for english, (_, sources) in [max(counts.items(), key=lambda item: item[1][0])]}
                                                                                                  
                                                                                                        
    tables = paired_quote_tables(narration[quotes_entry], donor[WO2_X360.name, wo2_quotes], wo2_donor.order)[2:]
    for m, d in ((m, d) for ours, theirs in tables for m, d in zip(ours, theirs, strict=False)):
        for japanese, english in _decoded([(quote_text(m), quote_text(d))]):
            if m[2:] != japanese and english != official[japanese][0]:
                official[m[2:]] = (english, wo2_donor.source)
    return official
