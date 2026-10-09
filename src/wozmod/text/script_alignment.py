"""Pair stock text with donor records using the migration baseline rules."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable

from wozmod.formats.linkdata import (
    decoded_member, entry_bytes, moz_entries, parse_index, wo1_entries, wo2_members,
)
from wozmod.formats.scripts import (
    DONOR_LETTERS, DIALOG_SUFFIX, EVENT_LINE, SECTIONS,
    Line, Script, ScriptError, Table, caption_cues, donor_text,
    event_lines, mesc_blocks, parse_script, quote_tables, quote_text,
)

WO1_SCRIPTS = range(579, 639)


WO2_SCRIPTS = range(2796, 2864)


CAPTIONS = range(146, 171)


CAPTION_MEMBER = 2793


DREAM_SCRIPTS = tuple(zip(range(40, 68), range(2836, 2864)))


MIN_SHARED_VOICES = 0.9


NARRATION = ((4071, "wo2", 60), (4073, "wo1", 498), (4103, "wo2", 59), (4105, "wo1", 509))


BRIEFINGS = (4190, range(101, 129))


EVENTS = ((range(177, 239), "wo2", 2692), (range(245, 283), "wo1", 395),
          (range(283, 384), "wo1", 473), (range(401, 402), "wo1", 456))


QUOTES = (4084, 512, 45)


QUOTE_INSERT = (94, 2)


X360_ENS = {45: 17, 59: 32, 60: 8, **{m: m - 26 for m in range(101, 129)},
            **{m: m - 2693 for m in range(2796, 2969)}}


def _parse(data: bytes) -> Script | None:
    try:
        return parse_script(data)
    except ScriptError:
        return None


def moz_scripts(bin_path: Path, idx_path: Path) -> dict[int, Script]:
    """Read every single-member four-section stock script."""
    index = parse_index(idx_path.read_bytes(), bin_path.stat().st_size)
    scripts = {}
    with bin_path.open("rb") as stream:
        for entry in index.entries:
            if len(entry.stored_sizes) != 1:
                continue
            data = entry_bytes(stream, entry)
            if data[:4] == b"\x04\0\0\0" and (parsed := _parse(data)):
                scripts[entry.ordinal] = parsed
    return scripts


def wo1_scripts(lnk_path: Path, idx_path: Path) -> dict[int, Script]:
    """Read the WO1 PC Europe stage scripts."""
    return {o: parse_script(d)
            for o, d in wo1_entries(lnk_path, idx_path, WO1_SCRIPTS).items()}


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
    found = wo1_entries(ens.with_suffix(".lnk"), ens.with_suffix(".idx"), sorted({X360_ENS[m] for m in members}))
    return {m: found[X360_ENS[m]] for m in members}


def wo2_ps2(iso_path: Path) -> Wo2Donor:
    return Wo2Donor(partial(wo2_members, iso_path), "<", "wo2-ps2")


def wo2_x360(ens: Path) -> Wo2Donor:
    return Wo2Donor(partial(x360_members, ens), ">", "wo2-x360")


def wo2_scripts(iso_path: Path) -> dict[int, Script]:
    """Read the WO2 PS2 USA stage scripts."""
    return wo2_ps2(iso_path).scripts()


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
                  for m, d in zip(moz.notices.records, donor.notices.records)]
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
                    for m, d in zip(getattr(moz, table).records, getattr(donor, table).records))


def _decoded(pairs) -> list[tuple[bytes, str]]:
    """Keep pairs with nonblank stock text and valid nonblank donor English."""
    return [(jp, text) for jp, en in pairs
            if jp.strip(b" \n\x81\x40") and en and (text := donor_text(en)) and text.strip()]


def narration_pairs(moz: bytes, donor: bytes) -> list[tuple[bytes, str]]:
    """Pair narration positions inside equal-length blocks."""
    return _decoded(pair for a, b in zip(mesc_blocks(moz), mesc_blocks(donor))
                    if len(a) == len(b) for pair in zip(a, b))


def event_pairs(moz: bytes, donor: bytes, order: str = "<") -> list[tuple[bytes, str]]:
    """Pair event subtitles by line index using the donor word order."""
    english = event_lines(donor, order)
    return _decoded((t, english[i]) for i, t in event_lines(moz).items() if i in english)


def caption_pairs(moz: bytes, donor: bytes) -> list[tuple[bytes, bytes]]:
    """Join each donor cue to the stock cue with the greatest positive overlap."""
    ours, joined = caption_cues(moz), {}
    for start, end, text in caption_cues(donor):
        overlap, at = max((min(end, e) - max(start, s), i) for i, (s, e, _) in enumerate(ours))
        if overlap > 0 and text.strip():
            joined.setdefault(at, []).extend(text.split())
    return [(ours[at][2], b" ".join(words)) for at, words in joined.items()]


def paired_quote_tables(moz: bytes, donor: bytes, order: str = "<") -> list[tuple[list[bytes], list[bytes]]]:
    """Pair format and officer tables, leaving inserted stock officers unpaired."""
    ours, theirs = quote_tables(moz), quote_tables(donor, order)
    at, count = QUOTE_INSERT
    theirs[2 + at:2 + at] = [[]] * count
    return list(zip(ours, theirs))


def quote_pairs(moz: bytes, donor: bytes, officers: bool = True, order: str = "<") -> list[tuple[bytes, str]]:
    """Pair notice formats by ordinal and optionally officer quotes by slot."""
    tables = paired_quote_tables(moz, donor, order)
    tables = tables if officers else tables[:2]
    return _decoded((quote_text(m), quote_text(d)) for a, b in tables for m, d in zip(a, b) if m and d)


def official_pairs(stock: Path, wo1: Path, wo2_donor: Wo2Donor) -> dict[bytes, tuple[str, str]]:
    """Re-derive stock bytes to (English, source), preserving baseline votes and suffixes."""
    lnk, idx = wo1.with_suffix(".lnk"), wo1.with_suffix(".idx")
    moz = moz_scripts(stock / "LINKDATA2.BIN", stock / "LINKDATA2.IDX")
    wo1_by_ordinal, wo2 = wo1_scripts(lnk, idx), wo2_donor.scripts()
    wo1_ids = {id(d) for d in wo1_by_ordinal.values()}
    found = [(pairs, "wo1" if id(d) in wo1_ids else "wo2") for pairs, d in (
        (record_pairs(moz[o], d), d)
        for o, d in pair_scripts(moz, [*wo1_by_ordinal.values(), *wo2.values()]).items())]
    found += [(prefix_pairs(moz[o], wo2[m]), "wo2") for o, m in DREAM_SCRIPTS]
    events = [(o, source, o + shift) for ordinals, source, shift in EVENTS for o in ordinals]
    briefing_entry, briefing_members = BRIEFINGS
    quotes_entry, wo1_quotes, wo2_quotes = QUOTES
    wanted = {"wo1": {wo1_quotes}, "wo2": {*briefing_members, wo2_quotes}}
    for _, source, entry in [*NARRATION, *events]:
        wanted[source].add(entry)
    donor = {("wo1", k): v for k, v in wo1_entries(lnk, idx, sorted(wanted["wo1"])).items()}
    donor |= {("wo2", k): v for k, v in wo2_donor.members(sorted(wanted["wo2"])).items()}
    narration = moz_entries(stock / "LINKDATA1.BIN", stock / "LINKDATA1.IDX",
                            [*(n for n, _, _ in NARRATION), briefing_entry, quotes_entry])
    found += [(narration_pairs(narration[n], donor[s, d]), s) for n, s, d in NARRATION]
                                                                                             
    found += [(quote_pairs(narration[quotes_entry], donor["wo1", wo1_quotes], officers=False), "wo1"),
              (quote_pairs(narration[quotes_entry], donor["wo2", wo2_quotes], order=wo2_donor.order), "wo2")]
    found.append((narration_pairs(narration[briefing_entry], b"".join(
        decoded_member(donor["wo2", m]) for m in briefing_members)), "wo2"))
    scenes = moz_entries(stock / "LINKDATA2.BIN", stock / "LINKDATA2.IDX", [o for o, _, _ in events])
    found += [(event_pairs(scenes[o], donor[s, d], wo2_donor.order if s == "wo2" else "<"), s) for o, s, d in events]
    movies = moz_entries(stock / "LINKDATA2.BIN", stock / "LINKDATA2.IDX", CAPTIONS)
    captions = wo2_donor.members([o + CAPTION_MEMBER for o in CAPTIONS])
    found += [(_decoded(caption_pairs(movies[o], captions[o + CAPTION_MEMBER])), "wo2") for o in CAPTIONS]
    votes: dict[bytes, dict[str, list]] = {}
    for pairs, source in found:
        for japanese, english in pairs:
            vote = votes.setdefault(japanese, {}).setdefault(english, [0, set()])
            vote[0] += 1
            vote[1].add(source)
    official = {japanese: (english, "wo1-pc" if "wo1" in sources else wo2_donor.source)
                for japanese, counts in votes.items()
                for english, (_, sources) in [max(counts.items(), key=lambda item: item[1][0])]}
                                                                                                  
                                                                                                        
    tables = paired_quote_tables(narration[quotes_entry], donor["wo2", wo2_quotes], wo2_donor.order)[2:]
    for m, d in ((m, d) for ours, theirs in tables for m, d in zip(ours, theirs)):
        for japanese, english in _decoded([(quote_text(m), quote_text(d))]):
            if m[2:] != japanese and english != official[japanese][0]:
                official[m[2:]] = (english, wo2_donor.source)
    return official
