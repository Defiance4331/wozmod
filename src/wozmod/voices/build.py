"""Build voice banks from stock and extracted donor files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wozmod import game_files
from wozmod.donors import WO1_X360, WO2_PSP, WO2_X360, DonorsFolder
from wozmod.formats.audio import (
    WO1_EVENT_VOICES, WO2_EVENT_VOICES, BankClips, VoiceBankError, decoded_clips, english_se_bank, english_voice_bank,
    english_voice_clips, parse_kovs_record,
)
from wozmod.formats.linkdata import LINKDATA2_FIRST, archive, decoded_member, ps2_members
from wozmod.formats.scripts import Script, guarded, parse_script
from wozmod.text.resolver import DonorResolver
from wozmod.text.script_alignment import (
    EVENTS, StageJoin, PSP_QUOTE_INSERT, PSP_QUOTES, PSP_STAGE_MEMBERS, QUOTES, WO1_QUOTE_INSERT, donor_event_entries, moz_scripts,
    dream_teams, paired_quote_tables, stage_joins, wo1_scripts, wo2_x360,
)

from . import phones
from .dream_events import DREAM_EVENTS, build_dream_events
from .dream_movies import DREAM_MOVIES, build_dream_movies
from .events import FOLDER, build_event_schedules
from .lips import donor_lips, english_lips, lip_bank, stock_lips, wo1_fits, x360_fits
from .maps import (
    VoiceSlot, dialogue_voice_maps, psp_voice_map, quote_copies, slot_records, slot_table, stage_triggers, voice_slots,
    with_copies,
)


VOICE_DONOR_FILES = (
    "linkdata_ens.idx", "linkdata_ens.lnk", "linkdata_ans.idx", "link_vodat.bdx",
    "link_sebank.hdx", "link_sebank.bdx",
)
PSP_TEXT, PSP_VOICES = "PSP_GAME/USRDIR/LINKDATA_NA.FNS", "PSP_GAME/USRDIR/sound/voice_na.bns"
PSP_SHOUTS, PSP_ENGLISH_SHOUTS = "PSP_GAME/USRDIR/LINKDATA.ENS", 1  # the English sound pack (EBOOT 0x1BEE48)
# The PSP movies whose English soundtracks the voices cut: the Dream movies, and the Z Dream scenes (events 402-413).
PSP_MOVIES = (*DREAM_MOVIES.values(), *DREAM_EVENTS.values())
PSP_VOICE_FILES = (PSP_TEXT, PSP_VOICES, PSP_SHOUTS, *PSP_MOVIES)


def psp_text_voice_map(moz: dict[int, Script], quotes: bytes, fns: Path) -> dict[int, int]:
    """Stock voice id to WO2 PSP clip, from the stock scripts and quote tables and the PSP text archive."""
    psp = ps2_members(fns, [PSP_QUOTES, *PSP_STAGE_MEMBERS.values()])  # the quotes are stored unpacked
    return psp_voice_map(moz, {o: parse_script(decoded_member(psp[m])) for o, m in PSP_STAGE_MEMBERS.items()},
                         paired_quote_tables(quotes, psp[PSP_QUOTES], insert=PSP_QUOTE_INSERT))


@dataclass(frozen=True)
class VoiceMaps:
    """The installer's dialogue voice maps, stock voice id (or voice slot) to donor record per donor, and the stock
    scripts and stage joins they come from. Everything that asks which take a line plays reads these (voice_maps)."""

    moz: dict[int, Script]
    joins: dict[int, StageJoin]
    triggers: dict[tuple[int, int], tuple[tuple[int, ...], str, int]]  # maps.stage_triggers
    slots: dict[tuple[int, int], VoiceSlot]  # maps.voice_slots
    wo1: dict[int, int]
    wo2: dict[int, int]
    psp: dict[int, int]

    @property
    def by_donor(self) -> dict[str, dict[int, int]]:
        """The maps in the donor order the bank takes clips in: WO2 X360, WO1 X360, WO2 PSP."""
        return {WO2_X360.name: self.wo2, WO1_X360.name: self.wo1, WO2_PSP.name: self.psp}


def voice_maps(stock: Path, donors: Path) -> VoiceMaps:
    """The dialogue voice maps from the stock game folder and the extracted donors folder: stage lines by trigger,
    the fixed runs, the quote slots and their copies, the Dream lines and the voice slots."""
    folders = DonorsFolder(Path(donors))
    wo1, wo2, psp = folders.folder("wo1-x360"), folders.folder("wo2-x360"), folders.folder("wo2-psp")
    moz = moz_scripts(Path(stock) / "LINKDATA2")
    wo2_donor = wo2_x360(wo2 / "linkdata_ens")
    wo1_ens = archive(wo1 / "linkdata_ens")
    quotes_entry, wo1_quotes, wo2_quotes = QUOTES
    quotes = game_files.archive(Path(stock) / "LINKDATA1")[quotes_entry]
    wo2_tables = paired_quote_tables(quotes, wo2_donor.members([wo2_quotes])[wo2_quotes], wo2_donor.order)
    wo1_by_ens, wo2_by_member = wo1_scripts(wo1 / "linkdata_ens"), wo2_donor.scripts()
    joins = stage_joins(moz, wo1_by_ens, wo2_by_member, teams=dream_teams(folders.program(WO2_X360.name)))
    one, two = dialogue_voice_maps(moz, joins,
                                   paired_quote_tables(quotes, wo1_ens[wo1_quotes], ">", insert=WO1_QUOTE_INSERT),
                                   wo2_tables)
    triggers = stage_triggers(moz, joins)
    # A line that shares its voice id with a line whose trigger plays another take, or that keeps its stock take
    # (maps.STOCK_TAKES), gets an id of its own.
    slots = voice_slots(moz, triggers, one, two)
    one, two = one | slot_records(slots, WO1_X360.name), two | slot_records(slots, WO2_X360.name)
    three = psp_text_voice_map(moz, quotes, psp / PSP_TEXT)
    stock_voices = game_files.voices(Path(stock))
    lengths: dict[int, int] = {}

    def length(voice: int) -> int:
        if voice not in lengths:
            lengths[voice] = parse_kovs_record(stock_voices.clips([voice])[voice], ordinal=voice)[2].sample_count
        return lengths[voice]

    with_copies([two, one, three], quote_copies([ours for ours, _ in wo2_tables], length))
    return VoiceMaps(moz, joins, triggers, slots, one, two, three)


def guarded_stages(stages: dict[int, bytes], joins: dict[int, StageJoin]) -> dict[str, bytes]:
    """Each stock stage script in stages (by LINKDATA2 entry) with its stand-ins closed once the event each stands in
    for has run (decisions ledger text row 51; StageJoin.stand_ins, formats.scripts.guarded), by its path below the
    mod folder: the runtime serves it in place of the stock script only while the voices are English, so the Japanese
    voice keeps every stock event."""
    return {f"{FOLDER}/{LINKDATA2_FIRST + o}.bin": guarded(data, joins[o].stand_ins) for o, data in sorted(stages.items())}


def said(clips: dict[int, bytes], sources: dict[int, str], donor_events: dict, stock_events: list[tuple[int, bytes]],
         resolver: DonorResolver) -> dict[int, str]:
    """The English each clip of an event line says, where it is known. A clip from an Xbox 360 disc says the donor's
    own event line for its record, from donor_events {source: (voice id to record, event files, byte order, (first
    event record, event lines))}; a clip from the PSP (the Z Dream scenes) says what the game shows under it, the
    translation of its stock line from stock_events (voice base, event file)."""
    found = {}
    for source, (records, events, order, (first, count)) in donor_events.items():
        lines = phones.donor_lines(events, order)
        found |= {voice: lines[records[voice] - first] for voice in clips if sources[voice] == source
                  and voice in records and 0 <= records[voice] - first < count and records[voice] - first in lines}
    shown = {voice for voice in clips if sources[voice] == WO2_PSP.name}
    return found | phones.shown_lines(stock_events, shown, resolver)


def build_voice_banks(stock: Path, donors: Path, speech: Path, separator: Path, output_dir: Path,
                      jobs: int | None = None) -> None:
    """Write voice/Voice.bin, voice/Slots.bin, voice/Lips.bin and voice/SE.bin, the English Dream movies in
    movie/voice, and the retimed event files and guarded stage scripts (guarded_stages) in captions/voice, below the
    supplied mod folder; speech is the PocketSphinx model folder (phones.MODEL) the generated
    mouths' phones come from, separator the model folder (separation.MODEL) that takes the Z Dream scenes' English
    voice out of the PSP's movies; jobs caps the ffmpeg processes and the speech and separator threads."""
    stock, donors, speech, output_dir = Path(stock), Path(donors), Path(speech), Path(output_dir)
    folders = DonorsFolder(donors)
    wo1, wo2, psp = folders.folder("wo1-x360"), folders.folder("wo2-x360"), folders.folder("wo2-psp")
    maps = voice_maps(stock, donors)
    one, two, three, slots = maps.wo1, maps.wo2, maps.psp, maps.slots
    wo2_donor = wo2_x360(wo2 / "linkdata_ens")
    linkdata1, linkdata2 = game_files.archive(stock / "LINKDATA1"), game_files.archive(stock / "LINKDATA2")
    wo1_ens = archive(wo1 / "linkdata_ens")
    stock_clips = game_files.voices(stock)
    clips, sources = english_voice_clips(
        one, (wo1 / "linkdata_ans.idx").read_bytes(), wo1 / "link_vodat.bdx",
        two, (wo2 / "linkdata_ans.idx").read_bytes(), wo2 / "link_vodat.bdx",
        stock_clips, jobs, psp=(three, psp / PSP_VOICES),
        stock_ids={s.slot: s.voices[0] for s in slots.values()},
    )
    # The Z Dream scenes have no donor clip: their lines are cut from the PSP's movies of them, at the stock ids.
    dream_events = linkdata2.read(list(DREAM_EVENTS))
    for voice, record in build_dream_events(stock_clips, psp, dream_events, Path(separator), jobs)[0].items():
        if voice in clips:
            raise VoiceBankError(f"voice id {voice} has both a {sources[voice]} clip and a Z Dream scene's take")
        clips[voice], sources[voice] = record, WO2_PSP.name
    destination = output_dir / "voice"
    destination.mkdir(parents=True, exist_ok=True)
    with open(destination / "Voice.bin", "wb") as out:
        english_voice_bank(clips, out)
    # The bank written, its clips are read back from it as needed: what follows reads the event lines' clips, and
    # holding all of them (about 1 GB) to the end set the step's peak.
    with BankClips(destination / "Voice.bin") as clips:
        (destination / "Slots.bin").write_bytes(slot_table(slots))
        stock_events = linkdata2.read([o for ordinals, _, _ in EVENTS for o in ordinals])
        wo1_events = wo1_ens.read(donor_event_entries(WO1_X360.name))
        wo2_events = wo2_donor.members(donor_event_entries(WO2_X360.name))
        # Under the English voice each event subtitle ends with its English line: retimed event files, served by voice.
        build_event_schedules(stock_events, clips, output_dir, jobs,
                              {WO1_X360.name: (wo1_events, ">"), WO2_X360.name: (wo2_events, wo2_donor.order)})
        # A line with no donor block gets its stock block stretched to the English clip: the Z Dream ones too (0xAF3 +
        # index, formats.audio.event_voice).
        stock_bases = ([(WO2_EVENT_VOICES[0] if source == WO2_X360.name else WO1_EVENT_VOICES[0], stock_events[o])
                        for ordinals, source, _ in EVENTS for o in ordinals]
                       + [(WO2_EVENT_VOICES[0], data) for _, data in sorted(dream_events.items())])
        lips, _ = english_lips(clips, sources, stock_lips(stock_bases), {
            "wo1-x360": (one, donor_lips(wo1_events.values(), ">", *WO1_EVENT_VOICES[1:]), wo1_fits),
            "wo2-x360": (two, donor_lips(wo2_events.values(), wo2_donor.order, *WO2_EVENT_VOICES[1:]), x360_fits),
            "wo2-psp": (three, {}, x360_fits),  # the PSP plays its scenes as movies: no voiced line has a lip block
        }, lambda some, rate: decoded_clips(some, rate, jobs),
            said(clips, sources, {WO1_X360.name: (one, wo1_events.values(), ">", WO1_EVENT_VOICES[1:]),
                                  WO2_X360.name: (two, wo2_events.values(), wo2_donor.order, WO2_EVENT_VOICES[1:])},
                 stock_bases, DonorResolver(donors)), phones.Listener(speech, jobs))
        (destination / "Lips.bin").write_bytes(lip_bank(lips))
    stages = linkdata2.read([o for o, join in maps.joins.items() if join.stand_ins])
    for name, data in guarded_stages(stages, maps.joins).items():
        (output_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (output_dir / name).write_bytes(data)
    seidx, se = linkdata1.read([4056, 4057]).values()
    bank, _ = english_se_bank(
        seidx, se, (wo1 / "link_sebank.hdx").read_bytes(), wo1 / "link_sebank.bdx",
        (wo2 / "link_sebank.hdx").read_bytes(), wo2 / "link_sebank.bdx",
        ps2_members(psp / PSP_SHOUTS, [PSP_ENGLISH_SHOUTS])[PSP_ENGLISH_SHOUTS], jobs,
    )
    (destination / "SE.bin").write_bytes(bank)
    build_dream_movies(stock, psp, output_dir)
