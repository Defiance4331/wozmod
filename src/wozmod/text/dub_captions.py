"""English-voice caption schedules for the movies whose English dub has no official English captions.

The WO1 X360 movies mev02 to mev05 (our movie102 to movie105) show captions only on French and German consoles.
Their French schedules are timed to the dub, so each of our cues takes the time of one French cue and the English
the dub says there. The runtime serves wozmod/voice/entries/<key>.bin in place of the stock schedule while the
voices are English.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from typing import TYPE_CHECKING

from wozmod.formats.linkdata import LINKDATA2_FIRST
from wozmod.formats.scripts import caption_cues
from wozmod.runtime.english_runtime import encode_english

if TYPE_CHECKING:
    from .resolver import DonorResolver

PAL_TICKS = 25  # a French or German schedule counts 25 ticks a second
STOCK_TICKS = 29.97  # the stock schedule counts 29.97
FNS_ENTRIES = range(369, 373)
DUB_SCHEDULE_FILES = tuple(f"linkdata_fns/{entry}.bin" for entry in FNS_ENTRIES)


class DubCaptionError(ValueError):
    """The dub captions do not fit their French schedule."""


def stock_ticks(pal: int) -> int:
    """The stock schedule's count for a moment a French or German schedule counts as pal."""
    return int(pal / PAL_TICKS * STOCK_TICKS + 0.5)


def schedule(timing: bytes, cues: list[str]) -> bytes:
    """A stock-format schedule: the French schedule's times, converted, with one English text per cue."""
    times = [(start, end) for start, end, _ in caption_cues(timing)]
    if len(times) != len(cues):
        raise DubCaptionError(f"{len(cues)} captions for {len(times)} timed cues")
    texts = [encode_english(text) for text in cues]
    if any(b"\0" in text for text in texts):
        raise DubCaptionError("a caption holds a NUL")
    return (struct.pack("<I", len(times))
            + b"".join(struct.pack("<II", stock_ticks(start), stock_ticks(end)) for start, end in times)
            + b"".join(text + b"\0" for text in texts))


def build_dub_schedules(resolver: DonorResolver, data: Path, out: Path) -> None:
    """Write wozmod/voice/entries/<key>.bin under out for every movie in data. A cue is the dub's words, or a pointer
    to the donor string that holds the same words."""
    folder = out / "wozmod/voice/entries"
    folder.mkdir(parents=True, exist_ok=True)
    for movie in json.loads(data.read_text(encoding="utf-8"))["movies"]:
        disc, archive, entry = movie["timing"]
        if (disc, archive) != ("wo1-x360", "fns") or entry not in FNS_ENTRIES:
            raise DubCaptionError(f"{movie['slot']}: no pinned schedule at {movie['timing']}")
        timing = (resolver.donors.folder(disc) / f"linkdata_fns/{entry}.bin").read_bytes()
        cues = [resolver.resolve(cue) if isinstance(cue, list) else cue for cue in movie["cues"]]
        try:
            built = schedule(timing, cues)
        except DubCaptionError as error:
            raise DubCaptionError(f"{movie['slot']}: {error}") from error
        (folder / f"{LINKDATA2_FIRST + movie['entry']}.bin").write_bytes(built)
