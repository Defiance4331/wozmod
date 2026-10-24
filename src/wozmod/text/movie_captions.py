"""Movie caption schedules: every captioned movie's cues, each with its own English.

The game reads a movie's caption schedule (LINKDATA2 146 to 176, key 4251 + entry) when the movie starts. The
installer writes one schedule per captioned movie, so a cue takes its English by its movie and its place, never
from another line with the same Japanese:

- wozmod/captions/<key>.bin: the stock times, for the stock movie with the Japanese voices. A cue shows the table's
  English for its stock string, unless movie_captions.json gives it a row of its own. A cue whose string the game
  also shows somewhere else needs that row: one English per string cannot be right at every place.
- wozmod/captions/voice/<key>.bin: the times of the movie the English voices play. The runtime serves it in place of
  the other while the voices are English. The WO2 X360 movies (movie001 to 025) bring their own caption schedules:
  each cue shows its official English, or where movie_captions.json says so, the English of a stock cue. The WO1
  X360 movies mev02 to mev05 (movie102 to 105) have captions only on French and German consoles, so their cues
  take the French times (25 ticks a second) and the English the dub says (dub_captions.json).
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from typing import TYPE_CHECKING

from wozmod.formats.linkdata import LINKDATA2_FIRST, archive
from wozmod.formats.scripts import caption_cues
from wozmod.runtime.english_runtime import encode_english

from .script_alignment import CAPTION_MEMBER, CAPTIONS, X360_ENS
from .stock import stock_key

if TYPE_CHECKING:
    from .resolver import DonorResolver

PAL_TICKS = 25  # a French or German schedule counts 25 ticks a second
STOCK_TICKS = 29.97  # the stock schedule counts 29.97, as do the WO2 X360 ones
SCHEDULES = range(146, 177)  # the captioned movies; entry 145 has no cue
X360_SCHEDULES = {entry: X360_ENS[entry + CAPTION_MEMBER] for entry in CAPTIONS}  # entry -> WO2 X360 ENS ordinal
FNS_ENTRIES = range(369, 373)
DUB_SCHEDULE_FILES = tuple(f"linkdata_fns/{entry}.bin" for entry in FNS_ENTRIES)
FOLDER, VOICE_FOLDER = "wozmod/captions", "wozmod/captions/voice"


class MovieCaptionError(ValueError):
    """The caption data does not fit the movies it names."""


def slot(entry: int) -> str:
    """The movie file whose schedule entry is: movie001 to 025, movie102 to 105, movie200 and 201."""
    if entry not in SCHEDULES:
        raise MovieCaptionError(f"entry {entry} is not a movie caption schedule")
    return f"movie{entry - 145 if entry <= 170 else entry - 69 if entry <= 174 else entry + 25:03d}"


def stock_ticks(pal: int) -> int:
    """The stock schedule's count for a moment a French or German schedule counts as pal."""
    return int(pal / PAL_TICKS * STOCK_TICKS + 0.5)


def schedule(times: list[tuple[int, int]], texts: list[bytes]) -> bytes:
    """A stock-format schedule: u32 count, count (u32 start, u32 end), the NUL-terminated texts."""
    if len(times) != len(texts):
        raise MovieCaptionError(f"{len(texts)} captions for {len(times)} timed cues")
    if any(b"\0" in text for text in texts):
        raise MovieCaptionError("a caption holds a NUL")
    return (struct.pack("<I", len(times)) + b"".join(struct.pack("<II", *time) for time in times)
            + b"".join(text + b"\0" for text in texts))


def _place(item: dict, kind: str) -> tuple[int, int]:
    entry, cue = item.get("entry"), item.get("cue")
    if type(entry) is not int or type(cue) is not int or cue < 0 or item.get("slot") != slot(entry):
        raise MovieCaptionError(f"{kind} {item.get('slot')}: wrong entry, slot or cue")
    return entry, cue


def _rows(data: Path) -> tuple[dict, dict]:
    """The cues with a row of their own, and the English-voice cues that show a stock cue's English, by place."""
    payload = json.loads(data.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise MovieCaptionError("unsupported movie caption data")
    own, voice = {}, {}
    for row in payload["cues"]:
        place = _place(row, "cue")
        if place in own or not re.fullmatch("[0-9a-f]{64}", row.get("key", "")) or "ref" not in row and "en" not in row:
            raise MovieCaptionError(f"cue {row['slot']}.{row['cue']}: duplicate, or no stock hash or English")
        own[place] = row
    for row in payload["voice_cues"]:
        place = _place(row, "voice cue")
        if place in voice or place[0] not in X360_SCHEDULES or type(row.get("stock_cue")) is not int:
            raise MovieCaptionError(f"voice cue {row['slot']}.{row['cue']}: duplicate, or no stock cue")
        voice[place] = row["stock_cue"]
    return own, voice


def build_movie_schedules(stock: Path, resolver: DonorResolver, pairs: dict[bytes, bytes], data: Path,
                          dub: Path, out: Path) -> None:
    """Write both schedules of every captioned movie under out. pairs is the text table: stock bytes to English."""
    own, voice = _rows(data)
    folder, voice_folder = out / FOLDER, out / VOICE_FOLDER
    voice_folder.mkdir(parents=True, exist_ok=True)
    shown: dict[int, list[bytes]] = {}
    for entry, raw in archive(stock / "LINKDATA2").read(SCHEDULES).items():
        if not (cues := caption_cues(raw)):
            continue
        texts = []
        for cue, (_, _, text) in enumerate(cues):
            row = own.pop((entry, cue), None)
            if row is None:
                texts.append(pairs.get(text, text))
            elif (stock_key(text) or ("",))[0] != row["key"]:
                raise MovieCaptionError(f"cue {slot(entry)}.{cue}: the stock cue is not the string its row names")
            else:
                texts.append(encode_english(resolver.resolve_row(row)))
        shown[entry] = texts
        (folder / f"{LINKDATA2_FIRST + entry}.bin").write_bytes(schedule([cue[:2] for cue in cues], texts))
    if own:
        raise MovieCaptionError(f"rows for cues the stock movies lack: {sorted(own)}")
    for entry, ordinal in X360_SCHEDULES.items():
        if not (cues := caption_cues(resolver.entry("wo2-x360", ordinal))) or entry not in shown:
            continue
        texts = []
        for cue, (_, _, text) in enumerate(cues):
            if (entry, cue) in voice:
                at = voice.pop((entry, cue))
                if not 0 <= at < len(shown[entry]):
                    raise MovieCaptionError(f"voice cue {slot(entry)}.{cue}: no stock cue {at}")
                texts.append(shown[entry][at])
            else:  # the words of the cue, which the runtime lays out again in its own lines
                texts.append(encode_english(resolver.resolve(["wo2-x360", "ens", ordinal, "captions", [cue]]))
                             if text.strip() else b"")
        (voice_folder / f"{LINKDATA2_FIRST + entry}.bin").write_bytes(schedule([cue[:2] for cue in cues], texts))
    if voice:
        raise MovieCaptionError(f"voice cues the Xbox 360 movies lack: {sorted(voice)}")
    build_dub_schedules(resolver, dub, out)


def build_dub_schedules(resolver: DonorResolver, data: Path, out: Path) -> None:
    """Write the English-voice schedule of every movie in data. A cue is the dub's words, or a pointer to the donor
    string that holds the same words."""
    folder = out / VOICE_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    for movie in json.loads(data.read_text(encoding="utf-8"))["movies"]:
        disc, kind, entry = movie["timing"]
        if (disc, kind) != ("wo1-x360", "fns") or entry not in FNS_ENTRIES:
            raise MovieCaptionError(f"{movie['slot']}: no pinned schedule at {movie['timing']}")
        timing = (resolver.donors.folder(disc) / f"linkdata_fns/{entry}.bin").read_bytes()
        times = [(stock_ticks(start), stock_ticks(end)) for start, end, _ in caption_cues(timing)]
        texts = [encode_english(resolver.resolve(cue) if isinstance(cue, list) else cue) for cue in movie["cues"]]
        try:
            built = schedule(times, texts)
        except MovieCaptionError as error:
            raise MovieCaptionError(f"{movie['slot']}: {error}") from error
        (folder / f"{LINKDATA2_FIRST + movie['entry']}.bin").write_bytes(built)
