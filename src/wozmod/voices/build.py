"""Build voice banks from stock and extracted donor files."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from wozmod.formats.audio import (
    MOZ_VOICE_IDS, SE_BANKS, english_se_bank, english_voice_bank, english_voice_clips,
)
from wozmod.formats.linkdata import decoded_member, moz_entries, wo1_entries
from wozmod.text.script_alignment import (
    QUOTES, moz_scripts, paired_quote_tables, wo1_scripts, wo2_x360,
)

from .maps import dialogue_voice_maps


VOICE_DONOR_FILES = (
    "linkdata_ens.idx", "linkdata_ens.lnk", "linkdata_ans.idx", "link_vodat.bdx",
    "link_sebank.hdx", "link_sebank.bdx",
)


@dataclass(frozen=True)
class VoiceBuildResult:
    voice: Path
    se: Path
    dialogue_tiers: dict[str, int]
    attack_tiers: dict[str, int]
    japanese_fallback: int
    english_se_banks: int


def build_voice_banks(stock: Path, donors: Path, output_dir: Path) -> VoiceBuildResult:
    """Write voice/Voice.bin and voice/SE.bin below the supplied mod folder."""
    stock, donors, output_dir = Path(stock), Path(donors), Path(output_dir)
    wo1, wo2 = donors / "wo1", donors / "wo2-x360"
    moz = moz_scripts(stock / "LINKDATA2.BIN", stock / "LINKDATA2.IDX")
    wo2_donor = wo2_x360(wo2 / "linkdata_ens")
    quotes_entry, wo1_quotes, wo2_quotes = QUOTES
    quotes = moz_entries(stock / "LINKDATA1.BIN", stock / "LINKDATA1.IDX", [quotes_entry])[quotes_entry]
    wo1_tables = paired_quote_tables(quotes, wo1_entries(
        wo1 / "linkdata_ens.lnk", wo1 / "linkdata_ens.idx", [wo1_quotes])[wo1_quotes])
    wo2_tables = paired_quote_tables(quotes, wo2_donor.members([wo2_quotes])[wo2_quotes], wo2_donor.order)
    one, two = dialogue_voice_maps(
        moz, wo1_scripts(wo1 / "linkdata_ens.lnk", wo1 / "linkdata_ens.idx"),
        wo2_donor.scripts(), wo1_tables, wo2_tables,
    )
    voiceidx = decoded_member(moz_entries(
        stock / "LINKDATA1.BIN", stock / "LINKDATA1.IDX", [4058])[4058])
    clips, dialogue_tiers = english_voice_clips(
        one, (wo1 / "linkdata_ans.idx").read_bytes(), wo1 / "link_vodat.bdx",
        two, (wo2 / "linkdata_ans.idx").read_bytes(), wo2 / "link_vodat.bdx",
        voiceidx, stock / "data/Sound/Voice/Voice.bin",
    )
    destination = output_dir / "voice"
    destination.mkdir(parents=True, exist_ok=True)
    voice_path = destination / "Voice.bin"
    voice_path.write_bytes(english_voice_bank(clips))
    seidx, se = moz_entries(stock / "LINKDATA1.BIN", stock / "LINKDATA1.IDX", [4056, 4057]).values()
    bank, attack_tiers = english_se_bank(
        seidx, se, (wo1 / "link_sebank.hdx").read_bytes(), wo1 / "link_sebank.bdx",
        (wo2 / "link_sebank.hdx").read_bytes(), wo2 / "link_sebank.bdx",
    )
    se_path = destination / "SE.bin"
    se_path.write_bytes(bank)
    english = sum(struct.unpack_from("<I", bank, 8 + 16 * b)[0] > 0 for b in range(SE_BANKS))
    return VoiceBuildResult(voice_path, se_path, dialogue_tiers, attack_tiers,
                            MOZ_VOICE_IDS - len(clips), english)
