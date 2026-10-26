"""Pair stock text with donor records using the migration baseline rules."""

from __future__ import annotations

import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import cached_property, partial
from itertools import accumulate
from pathlib import Path
from typing import Callable

from wozmod.donors import WO1_X360, WO2_PSP, WO2_X360
from wozmod.formats.linkdata import (
    archive, decoded_member,
)
from wozmod.formats.scripts import (
    LEAD, NOTICE, SPEAK, Script, ScriptError, caption_cues, donor_text, event_captions,
    event_lines, mesc_blocks, parse_script, quote_tables, quote_text, speak_flags,
)
from wozmod.movies import movie_rows

from .narration_voices import container

# WO1 X360 ENS ordinals, as are the other WO1 X360 entries below. Its loader 0x822A28C0 reads ENS k as archive id 0xA53 + k;
# 0x8219D948 loads the stage script ENS 88 + stage id.
WO1_SCRIPTS = range(88, 148)


WO2_SCRIPTS = range(2796, 2864)


# Both games load a stage's script by its stage id: MOZ 0x4F06D0 reads LINKDATA2 entry s (key 0x109B + s), WO2 X360
# 0x821B9BF0 ENS 103 + s (PS2 member 2796 + s, X360_ENS). The Dream stages are 40-67 in both. MOZ has 144 stages
# (the stage table, LINKDATA1 4075, counts them), so its scripts are LINKDATA2 0-143.
MOZ_SCRIPTS = range(144)
DREAM_SCRIPTS = tuple(zip(range(40, 68), range(2836, 2864), strict=True))
# WO2 PSP LINKDATA_NA.FNS members by stock Z Dream stage ordinal. The PSP loads a stage script as member stage id + 198
# (0x1524C8: 0x1066C(5, [0x126FE64] + 0xC6) at 0x1524E4), so these are PSP stages 72-83; that MOZ stage s is PSP stage
# s - 60 rests on the scripts alone (their voices fall in each PSP stage's range, table 0x25386C).
PSP_STAGE_MEMBERS = {ordinal: ordinal + 138 for ordinal in range(132, 144)}


MIN_SHARED_VOICES = 0.9


# Each narration entry's donor entry: WO1 X360 loads its still pages ENS 6 (0x8226F09C) and camp talk ENS 18
# (0x822912DC), WO2 X360 its Z pages ENS 8 (id 0xEF2, 0x822B05A8) and Z stills ENS 32 (0xF0A, 0x822DDE28): ids from
# 0xEEA read the English archive (0x8217B4B4), as ids from 0xA53 do on WO1. Their lines pair through narration_places.
NARRATION = ((4071, WO2_X360.name, 60), (4073, WO1_X360.name, 6), (4103, WO2_X360.name, 59), (4105, WO1_X360.name, 18))


# The briefing of Dream stage s. MOZ shows LINKDATA1 4190 block s - 0x28 for stages 0x28-0x43 and s - 0x68 for
# 0x84-0x8F (0x548C4A). WO2 X360 shows ENS s + 0xF0D - 0xEEA (0x822B05D0, the stage at [0x82575DD8 + 0x55E0]), PS2
# member s + 61, for the stages 40-67, which it numbers as MOZ does (DREAM_SCRIPTS). WO2 PSP shows LINKDATA_NA.FNS
# member s - 7 for 0x28-0x43 and s - 11 for its own Z Dream stages 0x48-0x53 (0x2111CC to 0x2111E0, the stage at
# 0x126FE64), which MOZ numbers 0x84-0x8F (the PSP loads stage script s + 198 at 0x1524E4, PSP_STAGE_MEMBERS); so
# block e is PSP member e + 33. Each block, entry and member holds one string, which each draw shows whole (PSP
# 0x2115B0). So block e pairs with PS2 member BRIEFINGS[1][e] (WO2 X360), and the blocks after them with the PSP
# members BRIEFINGS[2], which only the PSP holds.
BRIEFINGS = (4190, range(101, 129), range(61, 73))


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
# The WO2 PSP has every stock table: its loader 0x15AD9C reads LINKDATA_NA.FNS member 0x1B (PSP_QUOTES) into 0x6B
# officer tables after the two format tables, as MOZ 0x4F7160 does, and its pickers index them by the officer
# record's byte +5 as MOZ does (PSP 0x15AAE0, MOZ 0x4F6317), so none is inserted.
PSP_QUOTES = 27
PSP_QUOTE_INSERT = (0, 0)
# Inside a paired table, stock slot k pairs with donor slot k: the three games turn a battle event into a slot the
# same way. One switch on the event word dispatches each (MOZ 0x4F85D0, WO2 X360 0x821C0B18, WO1 X360 0x821A4230; the
# cases pair one to one) to the same slots, such as slot 7 (MOZ 0x4F685C, X360 0x821BE830, WO1 0x821A2210), or to the
# same base plus 3 times the place of the met officer in the speaker's list (MOZ 0x4F6220, X360 0x821BE360, WO1
# 0x821A1D60), and the pickers read that slot of the officer's table (MOZ 0x4F6160, X360 0x821BE2F8). Format entries
# pair the same way (event 1: MOZ 0x4F6FAD, X360 0x821BEF20, WO1 0x821A290C). WO1 lacks the events WO2 added, so it
# has slots 0-66 and format entries 0-13. No code in any of the games reads slots 16-21 or format table 1's entries 2
# and 10. The WO2 PSP's switch 0x15A5A8 (jump table 0x23A7A0) has the same cases and slots (slot 7: 0x159110; the
# rival step 0x157C08 adds 3 times the place; format event 1 reads entry 3 at 0x15A4D8), except that its event 12 has
# the event officer say slot 1 (0x158A98) where MOZ 0x4F7AA0 picks 1, 2, 3 or 22 + 3i; the PSP data still labels
# each line of those slots with its slot (@SVnnn_kk, kk = k). The PSP voices only a line ending @NNNN (slots 0, 1 and
# 70): 0x1568C4 reads NNNN - 1 and 0x1BE014 plays it (formats.audio.PSP_CLIP_BASE).
QUOTE_SLOTS = (*range(16), *range(22, 71))


def quote_slot_read(table: int, slot: int) -> bool:
    """Whether the quote code of any of the games reads slot of quote table (the join above): every entry of the two
    format tables, and an officer table's QUOTE_SLOTS. A line in another slot is never shown, so never voiced."""
    return table < 2 or slot in QUOTE_SLOTS


# WO2 X360 ENS ordinals by PS2 member id; ENS k is archive id 0xEEA + k. A stage script is ENS 103 + stage id
# (0x821B9BF0 asks for archive id 0xF51 + stage), PS2 member 2796 + stage id. The officer quotes are ENS 17 (0xEFB,
# 0x821BF0B0, once at start), the Z stills ENS 32 and the Z pages ENS 8 (NARRATION), and the Dream briefings ENS 75-102
# (BRIEFINGS).
X360_ENS = {45: 17, 59: 32, 60: 8, **{m: m - 26 for m in range(101, 129)},
            **{m: m - 2693 for m in range(2796, 2969)}}


def _parse(data: bytes) -> Script | None:
    try:
        return replace(parse_script(data), speaks=speak_flags(data))
    except ScriptError:
        return None


def moz_scripts(stem: Path) -> dict[int, Script]:
    """Read every single-member four-section stock stage script. Only the stage scripts are read: the build reads
    nothing else of LINKDATA2 that it does not need, so another mod's change there does not stop the install."""
    entries = archive(stem)
    single = entries.read(o for o in MOZ_SCRIPTS if o in entries and entries.parts(o) == 1)
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
        if not voices or not donor_voices:
            continue
        best = max(donor_voices, key=lambda v: len(voices & v[0]) / len(voices | v[0]))
        if len(voices & best[0]) / len(voices | best[0]) >= MIN_SHARED_VOICES:
            pairs[ordinal] = best[1]
    return pairs


# A stage script's records pair with a donor script's through the commands that show them, not through their places
# in the tables, as MOZ inserts, drops and moves records and events. The command dispatchers are MOZ 0x4F3AB0, WO2 X360
# 0x821BC1E4 and WO1 X360 0x8219FD28; scripts.NOTICE and SPEAK cite the handlers and the words they read. The speaker word of a speak
# command names a person; MOZ numbers its persons with insertions (measured: WO2 X360 persons 0-90 are MOZ 0-90,
# 97-567 are +16, 631-789 are +53), so person_map learns the numbering from the scripts themselves.


def _commands(script: Script, speaker: Callable[[int], object] | None = None) -> list[tuple[tuple, tuple | None]]:
    """The command stream as (token, command), each event closed by an end token. A command's token is its opcode,
    and a speak's also its speaker as speaker gives it (speaker None leaves speakers out)."""
    stream = []
    for event in script.events:
        stream += [((c[0], speaker(c[1])) if speaker and c[0] == SPEAK else (c[0],), c) for c in event]
        stream.append((("end",), None))
    return stream


def _aligned(ours: list, theirs: list) -> list[tuple[int, int, int]]:
    """(our index, their index, length of the matching run) for each pair of equal tokens SequenceMatcher aligns."""
    blocks = SequenceMatcher(None, ours, theirs, autojunk=False).get_matching_blocks()
    return [(block.a + k, block.b + k, block.size) for block in blocks for k in range(block.size)]


def _speak_votes(pairs: list[tuple[Script, Script]],
                 tokens: Callable[[Script, Script], tuple[Callable | None, Callable | None]],
                 wild: object = None) -> Counter:
    """(stock speaker, donor speaker) votes over paired stage scripts: each pair's commands are aligned with the speak
    tokens tokens gives for it (stock, donor; None leaves speakers out), and each aligned speak (whose stock token is
    wild, if given) votes for its counterpart's speaker with the length of the run it sits in."""
    votes: Counter = Counter()
    for moz, donor in pairs:
        ours, theirs = tokens(moz, donor)
        a, b = _commands(moz, ours), _commands(donor, theirs)
        for i, j, run in _aligned([t for t, _ in a], [t for t, _ in b]):
            if a[i][0][0] == SPEAK and (wild is None or a[i][0][1] == wild):
                votes[a[i][1][1], b[j][1][1]] += run
    return votes


def person_map(pairs: list[tuple[Script, Script]]) -> dict[int, int]:
    """Stock speaker to donor speaker over paired stage scripts, in two passes.

    First, the commands are aligned by opcode alone, and persons pair one to one, the largest vote first, so a person
    only stray matches reach maps to nothing. Then the commands are aligned again by speaker, where a stock person
    the first pass left unpaired, and a donor person none of whose stock persons speak in that stage, are both the
    same unknown token; each unpaired stock person takes the donor person of its largest vote there. That second
    pass finds the person whose only first-pass vote a copied event shifted onto a neighbour (WO2 X360 person 542,
    MOZ 558: MOZ copies Dream stage 47's events 32 and 33), and a donor person MOZ names by two ids (PSP persons 92
    and 93 are MOZ 605 and 618 in Z Dream stages 135 and 142, and MOZ 92 and 93 in stage 141), so several stock
    persons may share a donor person."""
    first = _speak_votes(pairs, lambda moz, donor: (None, None))
    persons, taken = {}, set()
    for (ours, theirs), _ in sorted(first.items(), key=lambda item: (-item[1], item[0][::-1])):
        if ours not in persons and theirs not in taken:
            persons[ours] = theirs
            taken.add(theirs)
    stock_of = {theirs: ours for ours, theirs in persons.items()}
    unknown = ("unknown",)

    def tokens(moz: Script, donor: Script) -> tuple[Callable, Callable]:
        here = {c[1] for event in moz.events for c in event if c[0] == SPEAK}
        return lambda p: persons.get(p, unknown), lambda p: p if stock_of.get(p) in here else unknown

    second = _speak_votes(pairs, tokens, unknown)
    for (ours, theirs), _ in sorted(second.items(), key=lambda item: (-item[1], item[0])):
        persons.setdefault(ours, theirs)
    return persons


def _record_pairs(ours: list, theirs: list) -> list[tuple[int, int]]:
    """(our record, their record) for each pair the table alignment makes: records with equal keys SequenceMatcher
    aligns, and the records between two aligned neighbours (or a table end), in order, where each side holds as many.
    That gap fill is order within an equal-count gap, not code: no donor command links these records. Its guard is
    speech recognition of the takes it gives (tests/test_subtitle_contexts.py,
    test_no_reviewed_voice_shows_a_neighbours_line), and stage_join's stream alignments override it.
    Those are records whose keys MOZ changed but not their place: a line the donor holds but no donor command speaks,
    which MOZ speaks, or a line MOZ gave another speaker. On the real data the fill pairs Dream stage 60 line 47,
    which the streams then give the donor line its trigger speaks, and story lines WO1 X360 94.29 and WO2 X360 5.13
    and 28.5, whose voice suffixes agree. A record MOZ inserts or drops leaves the counts unequal, so nothing there
    pairs."""
    pairs = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, ours, theirs, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            pairs += zip(range(i1, i2), range(j1, j2), strict=True)
    return pairs


def _record_keys(script: Script, table: str, key: Callable[[tuple], object]) -> list[tuple]:
    """Each record of a table by the commands that show it (the sorted keys of those commands), or unshown."""
    opcode, word = (SPEAK, 2) if table == "dialog" else (NOTICE, 1)
    found = defaultdict(set)
    for command in (c for event in script.events for c in event if c[0] == opcode):
        found[command[word]].add(key(command))
    return [tuple(sorted(found[r], key=repr)) if r in found else ("unshown",)
            for r in range(len(getattr(script, table).records))]


def stage_join(moz: Script, donor: Script, persons: dict[int, int]) -> dict[tuple[str, int], int]:
    """Pair a stock stage script's spoken lines with its donor script's: ("dialog", stock line) to donor line.

    Two alignments see a line: of its table, by the speakers of the speak commands that speak each line
    (_record_pairs), and of the command streams (opcodes, and speak commands by speaker). A line pairs by its table;
    one its table leaves unpaired (MOZ moved it: Dream stage 51 speaks lines 57 and 58 where WO2 X360 speaks its lines
    26 and 27) pairs by the streams, with a donor line no other line took. A line no command speaks pairs with
    nothing. Notices pair through notice_join.

    Both of those can be wrong where MOZ moved or swapped lines, so the optimal alignments of the streams decide last
    (the notice join's, below): a line whose speaks the earliest and the latest both put on one donor line takes it,
    and the line that held it by table or greedy stream loses it, unless the alignments put that line's own speak
    there too, when both keep it (the trigger: decisions ledger text rows 22 and 25). Dream stage 60 line 54 speaks
    WO2 X360 line 27 at its trigger, where stock line 27, other words by the same officer, has no counterpart; Dream
    stage 48 speaks lines 33 and 34 in the event where WO2 X360 speaks its lines 36 and 37, and the other way round,
    so the table's order pairs each with the other's sentence; Dream stage 60 speaks its new line 47 where WO2 X360
    speaks line 39 a second time. A line the alignments put on two donor lines keeps its pair (Dream stage 50 line 5:
    four of its events speak donor line 5, one donor line 8). The greedy match alone is never enough: Dream stage 46
    line 66 and twelve more lines match a donor line greedily that neither optimal alignment gives them."""
    def person(stock_person: int) -> object:
        return persons.get(stock_person, ("stock", stock_person))

    a, b = _record_keys(moz, "dialog", lambda c: person(c[1])), _record_keys(donor, "dialog", lambda c: c[1])
    joined = {("dialog", r): d for r, d in _record_pairs(a, b) if a[r] != ("unshown",)}
    ours, theirs = _commands(moz, person), _commands(donor, lambda p: p)
    spoken = defaultdict(set)
    for i, j, _ in _aligned([t for t, _ in ours], [t for t, _ in theirs]):
        if ours[i][0][0] == SPEAK:
            spoken[ours[i][1][2]].add(theirs[j][1][2])
    taken = set(joined.values())
    streamed = {r: d for r, (d, *more) in spoken.items() if ("dialog", r) not in joined and not more}
    pairs = joined | {("dialog", r): d for r, d in streamed.items() if d not in taken}
    earliest, latest = _alignment(moz, donor, persons, pairs, SPEAK)
    holders = defaultdict(set)
    for (_, r), d in pairs.items():
        holders[d].add(r)
    moved = {r: d for r, (d, *more) in latest.items() if not more and earliest.get(r) == {d}
             and pairs.get(("dialog", r)) != d}
    lost = {h for d in moved.values() for h in holders[d] - moved.keys()
            if d not in earliest.get(h, set()) | latest.get(h, set())}
    return {("dialog", r): d for (_, r), d in pairs.items() if r not in moved and r not in lost} | {
        ("dialog", r): d for r, d in moved.items()}


@dataclass(frozen=True)
class StageJoin:
    """A stock stage script (stock) and its donor script (source, ENS or FNS ordinal), stage_join's line pairs with
    it, and the stock persons' donor persons as person_map gives them."""

    source: str
    entry: int
    donor: Script
    pairs: dict[tuple[str, int], int]
    persons: dict[int, int]
    stock: Script

    @cached_property
    def notices(self) -> dict[tuple[str, int], int]:
        """("notices", stock notice) to donor notice, as notice_join pairs them."""
        return {("notices", r): d for r, d in notice_join(self).items()}


def stage_joins(moz: dict[int, Script], wo1: dict[int, Script], wo2: dict[int, Script],
                psp: dict[int, Script] | None = None) -> dict[int, StageJoin]:
    """Join each paired stock stage script (wo1 by ENS ordinal, wo2 by PS2 member id, psp by stock ordinal): story
    stages pair with the donor script that shares their voices (pair_scripts), Dream stages by DREAM_SCRIPTS, Z Dream
    stages by PSP_STAGE_MEMBERS; each donor's persons come from all of its pairs."""
    ordinal = {id(s): (WO1_X360.name, m) for m, s in wo1.items()} | {id(s): (WO2_X360.name, X360_ENS[m])
                                                                     for m, s in wo2.items()}
    paired = {o: (*ordinal[id(d)], d) for o, d in pair_scripts(moz, [*wo1.values(), *wo2.values()]).items()}
    paired |= {o: (WO2_X360.name, X360_ENS[m], wo2[m]) for o, m in DREAM_SCRIPTS if o in moz and m in wo2}
    paired |= {o: (WO2_PSP.name, PSP_STAGE_MEMBERS[o], d) for o, d in (psp or {}).items() if o in moz}
    persons = {source: person_map([(moz[o], d) for o, (s, _, d) in paired.items() if s == source])
               for source in (WO1_X360.name, WO2_X360.name, WO2_PSP.name)}
    return {o: StageJoin(source, entry, d, stage_join(moz[o], d, persons[source]), persons[source], moz[o])
            for o, (source, entry, d) in paired.items()}


# A notice pairs with the donor notice its command's counterpart shows. The counterparts come from an optimal
# alignment of the two command streams (each event's commands, then an end mark), not from the notice tables or a
# greedy stream match: MOZ reorders commands inside an event, splits, moves and copies events, and runs of notices
# share a format, so both of those drift (round 1 left 118 shown notices unpaired between them).
# - Two commands can be the same command when their opcodes agree, a speak's speaker maps (person_map), and a notice's
#   words 3 and 4 agree (NOTICE_WORDS). All three notice handlers read word 1 as the record, word 2 as a unit of the
#   stage's own table, word 3 as the variable the text shows and word 4 as its format (cases 0-4): MOZ 0x4F1890
#   (0x4F18A4, 0x4F18B6, 0x4F1903, 0x4F190E), WO2 X360 0x821BA268 (0x821BA280, 0x821BA288, 0x821BA2D8, 0x821BA2EC),
#   WO1 X360 0x8219DFD0 (0x8219DFE8, 0x8219DFF0, 0x8219E048, 0x8219E03C). MOZ renumbers the units, so word 2 is left
#   out. A notice MOZ edited (other words 3 and 4) pairs with nothing: the format says what the text holds (1 adds
#   the unit's officer name), and 29 of the 30 Dream notices MOZ gave another format gained a name.
# - A pair of commands scores 1 plus the likeness of their two events: the share of commands they have in common,
#   a speak counted with the donor line its own line pairs with (stage_join). A pair of commands that show text
#   scores more than all other pairs together, so likeness decides between the places a notice could go.
# - Where several alignments score the same, a notice pairs only where the earliest and the latest give it the same
#   donor notice, and a stock notice whose commands' counterparts show different donor notices pairs with none.
NOTICE_WORDS = (3, 4)


def _command_rows(script: Script, speaker: Callable[[int], object], line: Callable[[int], object]
                  ) -> tuple[list[tuple[tuple, tuple, tuple | None]], list[int]]:
    """(token, likeness token, command) for each command, each event closed by an end mark, and each row's event."""
    rows, events = [], []
    for e, commands in enumerate(script.events):
        for c in commands:
            if c[0] == SPEAK:
                token = (SPEAK, speaker(c[1]))
                rows.append((token, (*token, line(c[2])), c))
            else:
                token = (NOTICE, *(c[w] for w in NOTICE_WORDS)) if c[0] == NOTICE else (c[0],)
                rows.append((token, token, c))
            events.append(e)
        rows.append((("end",), ("end",), None))
        events.append(e)
    return rows, events


def _alignment(stock: Script, donor: Script, persons: dict[int, int], pairs: dict[tuple[str, int], int],
               opcode: int) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """The earliest and the latest optimal alignment of two stage scripts' command streams (see above; pairs are the
    stock lines' donor lines so far, persons the stock persons' donor persons), each as the donor records the
    counterparts of each stock record's opcode commands (NOTICE or SPEAK) show."""
    lines = {r: d for (table, r), d in pairs.items() if table == "dialog"}
    ours, oe = _command_rows(stock, lambda p: persons.get(p, ("stock", p)), lambda r: lines.get(r, ("stock", r)))
    theirs, te = _command_rows(donor, lambda p: p, lambda r: r)

    # Two events' likeness: the commands they have in common (likeness tokens, by count) over the larger event's
    # count, in thousandths, so that the scores are exact and ties compare equal.
    holders: dict[tuple, Counter] = defaultdict(Counter)
    for (_, bag, _), f in zip(theirs, te, strict=True):
        holders[bag][f] += 1
    sizes, their_sizes = Counter(oe), [te.count(f) for f in range(len(donor.events))]
    likeness = [[0] * len(their_sizes) for _ in stock.events]
    for (e, bag), count in Counter(zip(oe, (bag for _, bag, _ in ours), strict=True)).items():
        for f, their_count in holders[bag].items():
            likeness[e][f] += min(count, their_count)
    for e, row in enumerate(likeness):
        row[:] = [k * 1000 // max(sizes[e], their_size) for k, their_size in zip(row, their_sizes, strict=True)]
    n, m = len(ours), len(theirs)
    heavy = 2000 * (n + m) + 1

    def gain(i: int, j: int) -> int:
        """What pairing our row i with their row j scores: 0 unless their tokens agree."""
        if ours[i][0] != theirs[j][0]:
            return 0
        c = ours[i][2]
        return (heavy if c is not None and c[0] in (NOTICE, SPEAK) else 1000) + likeness[oe[i]][te[j]]

    places = defaultdict(list)
    for j, (token, _, _) in enumerate(theirs):
        places[token].append(j)
    pairs_at: dict[tuple, list[tuple[int, int]]] = {}  # (j, gain) for each place a row's token pairs, by row kind
    score = [[0] * (m + 1)]
    for i in range(n):
        # Rows rise along j, so a place where the tokens differ adds nothing to the row above: start from it, raise
        # each place j + 1 a pair at j reaches, then carry each maximum on along the row.
        if (kind := (ours[i][0], oe[i], ours[i][2] and ours[i][2][0])) not in pairs_at:
            pairs_at[kind] = [(j, gain(i, j)) for j in places[ours[i][0]]]
        above, row = score[-1], score[-1].copy()
        for j, g in pairs_at[kind]:
            if (value := above[j] + g) > row[j + 1]:
                row[j + 1] = value
        score.append(list(accumulate(row, max)))

    def traced(earliest: bool) -> dict[int, set[int]]:
        found, i, j = defaultdict(set), n, m
        while i and j:
            g = gain(i - 1, j - 1)
            here = score[i][j]
            pair = g > 0 and here == score[i - 1][j - 1] + g
            up, left = here == score[i - 1][j], here == score[i][j - 1]
            if pair and not (earliest and (up or left)):
                c = ours[i - 1][2]
                if c is not None and c[0] == opcode:
                    found[c[1 if opcode == NOTICE else 2]].add(theirs[j - 1][2][1 if opcode == NOTICE else 2])
                i, j = i - 1, j - 1
            elif up:
                i -= 1
            else:
                j -= 1
        return found

    return traced(True), traced(False)


def notice_join(join: StageJoin) -> dict[int, int]:
    """Each shown stock notice of a stage join's stock script with its donor notice: the one its commands'
    counterparts show (see above)."""
    earliest, latest = _alignment(join.stock, join.donor, join.persons, join.pairs, NOTICE)
    return {r: d for r, (d, *more) in latest.items() if not more and earliest.get(r) == {d}}


def joined_pairs(moz: Script, join: StageJoin, tables: tuple[str, ...]) -> list[tuple[bytes, str]]:
    """The stock and donor text of each record pair a stage join makes in tables (notices through notice_join)."""
    pairs = join.pairs | (join.notices if "notices" in tables else {})
    return _decoded((getattr(moz, t).records[r].text_bytes, getattr(join.donor, t).records[d].text_bytes)
                    for (t, r), d in pairs.items() if t in tables)


def _decoded(pairs) -> list[tuple[bytes, str]]:
    """Keep pairs with nonblank stock text and valid nonblank donor English."""
    return [(jp, text) for jp, en in pairs
            if jp.strip(b" \n\x81\x40") and en and (text := donor_text(en)) and text.strip()]


# A narration line pairs with the donor line the donor's draw shows for the same page, scene, record or command
# (tests/test_voice_blocks.py reads the places of both programs and joins the voices the same way):
# - 4073 / WO1 ENS 6, pages: page p shows line j of MESC block p (MOZ 0x5610DF; WO1 X360 0x8226F038 keeps the page at
#   [obj+0xB4] and the line at [obj+0xB9]).
# - 4105 / WO1 ENS 18, camp talk: line j of scene s is MESC line j plus the line counts of the scenes before s (MOZ
#   0x59B3D0, WO1 X360 0x8228F8F8); container entry s starts with its scene's line count, and the MESC is the last.
# - 4071 / WO2 ENS 8, Z pages: record k of stage s shows MESC line word 1; the stage's records are container entry
#   s + 1 (MOZ 0x55FB0E) or s + 4 (WO2 X360 0x822B09C8).
# - 4103 / WO2 ENS 32, Z stills: the i-th text command (kind 2) of scene n shows the MESC line in its u32 word 3
#   (MOZ 0x59AEC0, WO2 X360 0x822DC8DC); scene n is entry n + 2 (MOZ 0x599A75) or n + 5 (WO2 X360 0x822DCB24).
NARRATION_ENTRIES = {4071: (1, 4), 4103: (2, 5)}


def narration_places(entry: int, moz: bytes, donor: bytes) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Each shown stock narration line's (MESC block, line) with the donor's for the same page, scene, record or
    command (see above)."""
    if entry == 4073:
        return [((p, j), (p, j)) for p, (a, b) in enumerate(zip(mesc_blocks(moz), mesc_blocks(donor), strict=False))
                for j in range(min(len(a), len(b)))]
    ours, theirs = container(moz), container(donor)
    if entry == 4105:
        places, at = [], [0, 0]
        for a, b in zip(ours[:-1], theirs[:-1], strict=False):
            places += [((0, at[0] + j), (0, at[1] + j)) for j in range(min(a[0], b[0]))]
            at = [at[0] + a[0], at[1] + b[0]]
        return places
    first, donor_first = NARRATION_ENTRIES[entry]

    def shown(data: bytes, order: str) -> list[int]:
        if entry == 4071:
            return [struct.unpack_from(f"{order}4I", data, k)[1] for k in range(0, len(data), 16)]
        rows = (struct.unpack_from(f"{order}HHIII", data, k) for k in range(0, len(data) - 15, 16))
        return [text for kind, _, _, _, text in rows if kind == 2]

    return [((0, a), (0, b)) for k in range(min(len(ours) - first, len(theirs) - donor_first))
            for a, b in zip(shown(ours[first + k], "<"), shown(theirs[donor_first + k], ">"), strict=False)]


def narration_pairs(entry: int, moz: bytes, donor: bytes) -> list[tuple[bytes, str]]:
    """The stock and donor text of each narration line pair narration_places gives."""
    ours, theirs = mesc_blocks(moz), mesc_blocks(donor)
    return _decoded((ours[a][j], theirs[b][k]) for (a, j), (b, k) in narration_places(entry, moz, donor))


def briefing_pairs(moz: bytes, donors: list[bytes]) -> list[tuple[bytes, str]]:
    """The stock and donor text of each briefing: block e with the string of donors[e] (BRIEFINGS)."""
    return _decoded((block[0], mesc_blocks(donor)[0][0]) for block, donor in zip(mesc_blocks(moz), donors, strict=False))


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
    """Pair format and officer tables by officer id, leaving the stock officers the donor lacks unpaired; slot k of a
    table pairs with slot k of its donor table (QUOTE_SLOTS)."""
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
    # A spoken line, story or Dream, pairs with the donor line its speak command's counterpart speaks.
    found = [(joined_pairs(moz[o], j, ("notices", "dialog")), j.source)
             for o, j in stage_joins(moz, wo1_by_ordinal, wo2).items()]
    events = [(o, source, o + shift) for ordinals, source, shift in EVENTS for o in ordinals]
    briefing_entry, briefing_members, _ = BRIEFINGS
    quotes_entry, wo1_quotes, wo2_quotes = QUOTES
    wanted = {WO1_X360.name: {wo1_quotes}, WO2_X360.name: {*briefing_members, wo2_quotes}}
    for _, source, entry in NARRATION:
        wanted[source].add(entry)
    for source in wanted:
        wanted[source].update(donor_event_entries(source))
    donor = {(WO1_X360.name, k): v for k, v in archive(wo1).read(sorted(wanted[WO1_X360.name])).items()}
    donor |= {(WO2_X360.name, k): v for k, v in wo2_donor.members(sorted(wanted[WO2_X360.name])).items()}
    narration = linkdata1.read([*(n for n, _, _ in NARRATION), briefing_entry, quotes_entry])
    found += [(narration_pairs(n, narration[n], donor[s, d]), s) for n, s, d in NARRATION]
                                                                                             
    found += [(quote_pairs(narration[quotes_entry], donor[WO1_X360.name, wo1_quotes], officers=False, order=">",
                           insert=WO1_QUOTE_INSERT), WO1_X360.name),
              (quote_pairs(narration[quotes_entry], donor[WO2_X360.name, wo2_quotes], order=wo2_donor.order), WO2_X360.name)]
    found.append((briefing_pairs(narration[briefing_entry], [decoded_member(donor[WO2_X360.name, m])
                                                             for m in briefing_members]), WO2_X360.name))
    scenes = linkdata2.read([o for o, _, _ in events])
    lines = {s: donor_event_lines((donor[s, e] for e in donor_event_entries(s)), wo2_donor.order if s == WO2_X360.name
                                  else ">") for s in wanted}
    found += [(event_pairs(scenes[o], lines[s]), s) for o, s, _ in events]
    # WO1 X360 holds German, not English, at many of its caption sites, so only the WO2 X360 captions pair.
    found += [(event_caption_pairs(scenes[o], donor[s, d], wo2_donor.order), s) for o, s, d in events if s == WO2_X360.name]
    from .contexts import x360_schedules  # contexts imports this module
    member = {ens: m for m, ens in X360_ENS.items()}
    schedules = {o: member[ens] for o, ens in x360_schedules(movie_rows()).items()}  # stock entry -> PS2 member
    movies, captions = linkdata2.read(list(schedules)), wo2_donor.members(list(schedules.values()))
    # A movie cue takes its English by its place (text.movie_captions), so a movie caption votes only for a string
    # no other site shows: the row of a shared string serves the other sites.
    elsewhere = {japanese for pairs, _ in found for japanese, _ in pairs}
    found += [([pair for pair in _decoded(caption_pairs(movies[o], captions[m]))
                if pair[0] not in elsewhere], WO2_X360.name) for o, m in schedules.items()]
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
