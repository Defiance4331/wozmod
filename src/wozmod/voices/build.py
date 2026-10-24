"""Build voice banks from stock and extracted donor files."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from wozmod import resources
from wozmod.donors import WO1_X360, WO2_X360, DonorsFolder
from wozmod.formats.audio import (
    MOZ_VOICE_IDS, SE_BANKS, WO1_EVENT_VOICES, WO2_EVENT_VOICES, english_se_bank, english_voice_bank,
    english_voice_clips,
)
from wozmod.formats.linkdata import archive, decoded_member, ps2_members
from wozmod.formats.scripts import Script, parse_script, quote_tables
from wozmod.text.psp_alignment import PSP_STAGE_MEMBERS
from wozmod.text.script_alignment import (
    EVENTS, QUOTES, WO1_QUOTE_INSERT, donor_event_entries, moz_scripts, paired_quote_tables, wo1_scripts, wo2_x360,
)

from .events import build_event_schedules
from .lips import WO1_LINES, X360_LINES, donor_lips, english_lips, lip_bank, stock_lips, wo1_fits, x360_fits
from .maps import dialogue_voice_maps, psp_voice_map, shown_lines


VOICE_DONOR_FILES = (
    "linkdata_ens.idx", "linkdata_ens.lnk", "linkdata_ans.idx", "link_vodat.bdx",
    "link_sebank.hdx", "link_sebank.bdx",
)
PSP_TEXT, PSP_VOICES = "PSP_GAME/USRDIR/LINKDATA_NA.FNS", "PSP_GAME/USRDIR/sound/voice_na.bns"
PSP_SHOUTS, PSP_ENGLISH_SHOUTS = "PSP_GAME/USRDIR/LINKDATA.ENS", 1  # the English sound pack (EBOOT 0x1BEE48)
PSP_VOICE_FILES = (PSP_TEXT, PSP_VOICES, PSP_SHOUTS)
TEXT_TABLE = resources.root() / "data/text/table.json"


def psp_text_voice_map(moz: dict[int, Script], quotes: bytes, fns: Path) -> dict[int, int]:
    """Stock voice id to WO2 PSP clip, from the stock scripts and quote tables and the PSP text archive."""
    psp = ps2_members(fns, [27, *PSP_STAGE_MEMBERS.values()])  # member 27, the quotes, is stored unpacked
    return psp_voice_map(moz, {o: parse_script(decoded_member(psp[m])) for o, m in PSP_STAGE_MEMBERS.items()},
                         list(zip(quote_tables(quotes), quote_tables(psp[27]), strict=True)))


@dataclass(frozen=True)
class VoiceBuildResult:
    voice: Path
    se: Path
    dialogue_tiers: dict[str, int]
    attack_tiers: dict[str, int]
    japanese_fallback: int
    english_se_banks: int
    lip_tiers: dict[str, int]


def table_rows(table: Path = TEXT_TABLE) -> dict[str, dict]:
    """The translation rows by key: a stage line's row decides which donor line, and so which voice, it pairs with."""
    return {row["key"]: row for row in json.loads(table.read_text(encoding="utf-8"))["rows"]}


def build_voice_banks(stock: Path, donors: Path, output_dir: Path, jobs: int | None = None,
                      table: Path = TEXT_TABLE) -> VoiceBuildResult:
    """Write voice/Voice.bin, voice/Lips.bin and voice/SE.bin below the supplied mod folder; jobs caps the ffmpeg
    processes and table is the translation table."""
    stock, donors, output_dir = Path(stock), Path(donors), Path(output_dir)
    folders = DonorsFolder(donors)
    wo1, wo2, psp = folders.folder("wo1-x360"), folders.folder("wo2-x360"), folders.folder("wo2-psp")
    moz = moz_scripts(stock / "LINKDATA2")
    wo2_donor = wo2_x360(wo2 / "linkdata_ens")
    linkdata1, linkdata2, wo1_ens = archive(stock / "LINKDATA1"), archive(stock / "LINKDATA2"), archive(wo1 / "linkdata_ens")
    quotes_entry, wo1_quotes, wo2_quotes = QUOTES
    quotes = linkdata1[quotes_entry]
    wo1_tables = paired_quote_tables(quotes, wo1_ens[wo1_quotes], ">", insert=WO1_QUOTE_INSERT)
    wo2_tables = paired_quote_tables(quotes, wo2_donor.members([wo2_quotes])[wo2_quotes], wo2_donor.order)
    one, two = dialogue_voice_maps(
        moz, wo1_scripts(wo1 / "linkdata_ens"),
        wo2_donor.scripts(), wo1_tables, wo2_tables, shown_lines(moz, table_rows(table)),
    )
    three = psp_text_voice_map(moz, quotes, psp / PSP_TEXT)
    voiceidx = decoded_member(linkdata1[4058])
    clips, sources = english_voice_clips(
        one, (wo1 / "linkdata_ans.idx").read_bytes(), wo1 / "link_vodat.bdx",
        two, (wo2 / "linkdata_ans.idx").read_bytes(), wo2 / "link_vodat.bdx",
        voiceidx, stock / "data/Sound/Voice/Voice.bin", jobs, psp=(three, psp / PSP_VOICES),
    )
    dialogue_tiers = {tier: sum(s == tier for s in sources.values()) for tier in ("wo1-x360", "wo2-x360", "wo2-psp")}
    destination = output_dir / "voice"
    destination.mkdir(parents=True, exist_ok=True)
    voice_path = destination / "Voice.bin"
    voice_path.write_bytes(english_voice_bank(clips))
    stock_events = linkdata2.read([o for ordinals, _, _ in EVENTS for o in ordinals])
    wo1_events = wo1_ens.read(donor_event_entries(WO1_X360.name))
    wo2_events = wo2_donor.members(donor_event_entries(WO2_X360.name))
    # Under the English voice each event subtitle ends with its English line: retimed event files, served by voice.
    build_event_schedules(stock_events, clips, output_dir, jobs,
                          {WO1_X360.name: (wo1_events, ">"), WO2_X360.name: (wo2_events, wo2_donor.order)})
    lips, lip_tiers = english_lips(clips, sources, stock_lips(
        [(WO2_EVENT_VOICES[0] if source == WO2_X360.name else WO1_EVENT_VOICES[0], stock_events[o])
         for ordinals, source, _ in EVENTS for o in ordinals]), {
        "wo1-x360": (one, donor_lips(wo1_events.values(), ">", *WO1_LINES), wo1_fits),
        "wo2-x360": (two, donor_lips(wo2_events.values(), wo2_donor.order, *X360_LINES), x360_fits),
        "wo2-psp": (three, {}, x360_fits),  # the PSP plays its scenes as movies: no voiced line has a lip block
    })
    (destination / "Lips.bin").write_bytes(lip_bank(lips))
    seidx, se = linkdata1.read([4056, 4057]).values()
    bank, attack_tiers = english_se_bank(
        seidx, se, (wo1 / "link_sebank.hdx").read_bytes(), wo1 / "link_sebank.bdx",
        (wo2 / "link_sebank.hdx").read_bytes(), wo2 / "link_sebank.bdx",
        ps2_members(psp / PSP_SHOUTS, [PSP_ENGLISH_SHOUTS])[PSP_ENGLISH_SHOUTS], jobs,
    )
    se_path = destination / "SE.bin"
    se_path.write_bytes(bank)
    english = sum(struct.unpack_from("<I", bank, 8 + 16 * b)[0] > 0 for b in range(SE_BANKS))
    return VoiceBuildResult(voice_path, se_path, dialogue_tiers, attack_tiers,
                            MOZ_VOICE_IDS - len(clips), english, lip_tiers)
