from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

RATE, SPAN = 4000, 1 << 14  # compare clips as 4 kHz mono, their first 4 s


def _decode(job: tuple[str, bytes]) -> list[float]:
    form, data = job
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-f", form, "-i", "-", "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                         input=data, capture_output=True, check=True).stdout
    return pcm


def _spectra(jobs: list[tuple[str, bytes]]):
    """Each clip's normalised spectrum, for cross-correlation by product."""
    import numpy as np

    with ThreadPoolExecutor(os.process_cpu_count()) as pool:
        pcms = list(pool.map(_decode, jobs))
    out = []
    for pcm in pcms:
        x = np.frombuffer(pcm, dtype=np.int16)[:SPAN // 2].astype(np.float64)
        x -= x.mean() if len(x) else 0
        norm = np.linalg.norm(x)
        out.append(np.fft.rfft(x / norm, SPAN) if len(x) >= 400 and norm else None)
    return out


def _same_recording(a, b) -> bool:
    import numpy as np

    return a is not None and b is not None and float(np.max(np.abs(np.fft.irfft(a * np.conj(b), SPAN)))) > 0.5


@pytest.mark.skipif(os.environ.get("WOZMOD_SHOUT_BANKS") != "1" or not shutil.which("ffmpeg")
                    or not os.environ.get("WOZMOD_DONORS") or not os.environ.get("WOZMOD_GAME"),
                    reason="set WOZMOD_SHOUT_BANKS=1, WOZMOD_DONORS and WOZMOD_GAME, with ffmpeg")
def test_every_english_shout_bank_plays_its_own_officers_recordings() -> None:
    # Bug: a MOZ bank is paired with another officer's donor bank: by position onto WO2 X360's repeated banks
    # (San Zang played Zhao Yun's shouts), a donor gap in the wrong place (every later officer shifted), or the
    # PSP numbered otherwise than MOZ. The oracle is the recordings: the PSP's Japanese pack holds the stock
    # takes, and its English pack many of the Xbox 360 takes, each in the bank of PSP officer id + 8.
    from wozmod.formats.audio import (
        _vag, _xma2_riff, moz_se_banks, psp_se_entries, se_entries, se_plan, wo2_se_entries,
    )
    from wozmod.formats.linkdata import moz_entries, ps2_members
    from wozmod.voices.build import PSP_SHOUTS

    game, donors = Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"])
    seidx, se = moz_entries(game / "LINKDATA1.BIN", game / "LINKDATA1.IDX", [4056, 4057]).values()
    stock = moz_se_banks(seidx, se)
    packs = ps2_members(donors / "wo2-psp" / PSP_SHOUTS, [0, 1])
    banks = {disc: wo2_se_entries((donors / disc / "link_sebank.hdx").read_bytes()) for disc in ("wo1-x360", "wo2-x360")}
    banks["wo2-psp"] = psp_se_entries(packs[1])
    japanese = psp_se_entries(packs[0])
    plan = se_plan(stock, banks)
    files = {disc: (donors / disc / "link_sebank.bdx").read_bytes() for disc in ("wo1-x360", "wo2-x360")}

    def clip(disc: str, bank: int, j: int) -> bytes:
        offset, size, _ = banks[disc][bank][j]
        return packs[1][offset:offset + size] if disc == "wo2-psp" else files[disc][offset:offset + size]

    # No two MOZ banks play one donor bank, or the same clips.
    users, plays = {}, {}
    for moz, sources in plan.items():
        for disc, bank in {(disc, bank) for disc, bank, _ in sources}:
            users.setdefault((disc, bank), []).append(moz)
        plays.setdefault(tuple(hashlib.sha256(clip(*s)).digest() for s in sources), []).append(moz)
    shared = {f"{disc} {bank}": mozs for (disc, bank), mozs in users.items() if len(mozs) > 1}
    same = [mozs for mozs in plays.values() if len(mozs) > 1]

    # Each MOZ bank of a WO1 X360 and a WO2 X360 bank: the two hold one officer's clips (WO1 reuses WO2's bytes).
    disagree = []
    for moz, sources in plan.items():
        pair = {disc: bank for disc, bank, _ in sources if disc != "wo2-psp"}
        if len(pair) == 2:
            one = {hashlib.sha256(clip("wo1-x360", pair["wo1-x360"], j)).digest() for j in range(len(banks["wo1-x360"][pair["wo1-x360"]]))}
            two = {hashlib.sha256(clip("wo2-x360", pair["wo2-x360"], j)).digest() for j in range(len(banks["wo2-x360"][pair["wo2-x360"]]))}
            if len(one & two) * 2 < len(one):
                disagree.append(moz)

    # The officer banks (MOZ 9-104, PSP 8-103): the PSP Japanese bank with the most stock takes is the MOZ bank's
    # less one, and a PSP English bank sharing three or more takes with a planned Xbox 360 bank (one or two can be
    # a chance match of short grunts) is the same one.
    officers = range(9, 105)
    entries = {moz: len(se_entries(stock[moz][0])) for moz in officers}
    jobs, at = [], {}

    def add(key, form: str, data: bytes) -> None:
        at[key] = len(jobs)
        jobs.append((form, data))

    for moz in officers:
        header, data = stock[moz]
        for k, entry in enumerate(se_entries(header)):
            rate = struct.unpack_from("<H", header, entry + 4)[0]
            _, offset, size = struct.unpack_from("<III", header, entry + 16)
            body = data[12 + offset:12 + offset + size]
            fmt = struct.pack("<HHIIHHHH", 0x11, 1, rate, rate * 512 // 1017, 512, 4, 2, 1017)
            chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(body)) + body
            add(("moz", moz, k), "wav", b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WAVE" + chunk)
    for language, pack, table in (("ja", packs[0], japanese), ("en", packs[1], banks["wo2-psp"])):
        for bank in range(8, 104):
            for k, record in enumerate(table[bank]):
                if record:
                    add((language, bank, k), "vag", _vag(pack[record[0]:record[0] + record[1]], record[2]))
    for moz in officers:
        for k, (disc, bank, j) in enumerate(plan.get(moz, ())):
            if disc != "wo2-psp":
                add(("x360", moz, k), "wav", _xma2_riff(clip(disc, bank, j), banks[disc][bank][j][2]))
    spectra = _spectra(jobs)
    get = lambda key: spectra[at[key]] if key in at else None  # noqa: E731

    def best(key, language: str) -> tuple[int | None, int]:
        counts = {bank: sum(_same_recording(get((*key, k)), get((language, bank, k))) for k in range(entries[key[1]]))
                  for bank in range(8, 104)}
        bank = max(counts, key=counts.get)
        return (bank if counts[bank] else None), counts[bank]

    numbered, english = {}, {}
    for moz in officers:
        bank, count = best(("moz", moz), "ja")
        if bank != moz - 1 or count * 2 < entries[moz]:
            numbered[moz] = (bank, count)
        if any(disc != "wo2-psp" for disc, _, _ in plan.get(moz, ())):
            bank, count = best(("x360", moz), "en")
            if count >= 3 and bank != moz - 1:
                english[moz] = (bank, count)

    assert (shared, same, disagree, numbered, english) == ({}, [], [], {}, {})
