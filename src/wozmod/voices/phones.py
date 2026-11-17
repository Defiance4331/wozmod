"""The phones of English speech, per lip frame, for the generated mouths (lips.py).

PocketSphinx (built into wozmod-native) force-aligns a line's words to its clip, or, for a line whose words are not
known, hears its phones with no words. Its en-us model, the CMU dictionary and its phone language model are an input
folder of the installer, checked against MODEL: they are CMU's, not ours, so wozmod does not carry them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from wozmod import native, resources
from wozmod.formats.scripts import donor_text, event_lines
from wozmod.text.keys import event_voice_form, stock_key


PHONE_RATE = 16_000  # samples a second of the speech PocketSphinx's en-us model is made for
# The CMU phones; a phone's number in a context is its place here plus 1, and 0 is silence or a noise (lips.rs PHONES).
PHONES = ("AA AE AH AO AW AY B CH D DH EH ER EY F G HH IH IY JH K L M N NG OW OY P R S SH T TH UH UW V W Y Z ZH"
          .split())
NUMBER = {phone: n for n, phone in enumerate(PHONES, 1)}

# The model folder: each file it needs, (bytes, SHA-256). CMU Sphinx's US English model, dictionary and phone language
# model, as PocketSphinx 5.1.1's source release holds them under model/en-us/ (BSD licence; packaging's
# POCKETSPHINX-NOTICE.txt). The 27 MB word language model beside them is not needed.
MODEL = {
    "en-us/feat.params": (164, "c982c5f75e2a30c34d2c9ef1f6b129a5a00e67e3e927565076b58664cc2404c9"),
    "en-us/mdef": (2_959_176, "2360f9a86889c1cfee8bd618a0269387911e5fb2920a594f506b18b8c79683b0"),
    "en-us/means": (838_732, "832019e32cac12eb318964f96f469034acb12d0348eeddc3831831a100cb4dd4"),
    "en-us/noisedict": (56, "7295b07df2c204c4f87c6782b6be1a3859d7006d4e3864181c955d6dab105a33"),
    "en-us/sendump": (1_969_024, "8c9564c0d5bef69ca9d9bf1014abe162f071644cf02cf1fa8a483c3dc165a7a8"),
    "en-us/transition_matrices": (2_080, "c1f7f28ea43177be734be1f88bd7f1b9a853d0e660f8599c67c6eaeca8bb539a"),
    "en-us/variances": (838_732, "b00d696f85e96834fc10f8e5f06428d8c4db6bffdbe5845b6f69bf6efbc48fa5"),
    "cmudict-en-us.dict": (3_275_645, "20b5c293e1f311fb375fe067e500ec5636f4fc7af5594967263696def9b23bfe"),
    "en-us-phone.lm.bin": (857_195, "c57e0fa4191b096b1279cfe3a77927f52568fdecfc6624ddb5cec9527c763a54"),
}


def model_problem(folder: Path | str | None) -> str | None:
    """None when folder holds every MODEL file as pinned, else what is wrong, naming the first file that is."""
    if folder is None or not Path(folder).is_dir():
        return "folder not found"
    for name, (size, sha256) in MODEL.items():
        path = Path(folder) / name
        if not path.is_file():
            return f"{name} is missing"
        with path.open("rb") as stream:
            if path.stat().st_size != size or hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
                return f"{name} is not the supported file (wrong size or SHA-256)"
    return None


def words(text: str) -> list[str]:
    """The words of a line as the dictionary spells them: lower case, accents dropped, a hyphen between words, other
    punctuation and the game's control codes left out, an apostrophe kept inside a word."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().replace("-", " ")
    return [w for w in (token.strip("'") for token in re.findall(r"[a-z0-9']+", plain)) if w]


# Letter groups to phones, the longest group first where two start alike: our own rules for the words the dictionary
# lacks, which in the game's lines are nearly all romanised Chinese and Japanese names, plus a few interjections.
RULES = (
    ("tch", "CH"), ("sh", "SH"), ("ch", "CH"), ("ts", "T S"), ("th", "TH"), ("ph", "F"), ("ng", "NG"),
    ("ck", "K"), ("ll", "L"), ("rr", "R"), ("ss", "S"), ("mm", "M"), ("nn", "N"), ("gg", "G"), ("hh", "HH"),
    ("ai", "AY"), ("ei", "EY"), ("ao", "AW"), ("au", "AW"), ("ou", "OW"), ("oo", "UW"), ("ee", "IY"),
    ("ie", "IY EH"), ("ia", "IY AA"), ("io", "IY OW"), ("ua", "W AA"), ("ue", "UW EH"), ("ui", "W IY"),
    ("uo", "W OW"), ("oa", "OW AH"), ("ea", "IY"), ("oi", "OY"), ("'s", "Z"), ("y", "Y"), ("x", "SH"),
    ("q", "CH"), ("z", "Z"), ("j", "JH"), ("a", "AA"), ("e", "EH"), ("i", "IY"), ("o", "OW"), ("u", "UW"),
    ("b", "B"), ("c", "K"), ("d", "D"), ("f", "F"), ("g", "G"), ("h", "HH"), ("k", "K"), ("l", "L"),
    ("m", "M"), ("n", "N"), ("p", "P"), ("r", "R"), ("s", "S"), ("t", "T"), ("v", "V"), ("w", "W"),
)


def spelled(word: str) -> list[str]:
    """A word's phones by RULES, each group matched at the first place it fits, a phone said twice in a row once;
    characters no rule reads (digits, apostrophes) say nothing."""
    out, at = [], 0
    while at < len(word):
        group = next(((letters, phones) for letters, phones in RULES if word.startswith(letters, at)), None)
        if group is None:
            at += 1
            continue
        out += group[1].split()
        at += len(group[0])
    return [phone for n, phone in enumerate(out) if n == 0 or phone != out[n - 1]]


@cache
def dictionary(folder: Path) -> frozenset[str]:
    """The words the model folder's dictionary spells (a word's alternative spellings, word(2), count as it)."""
    text = (folder / "cmudict-en-us.dict").read_text(encoding="utf-8")
    return frozenset(re.sub(r"\(\d+\)$", "", line.split(maxsplit=1)[0]) for line in text.splitlines() if line.strip())


Segments = list[tuple[str, int, int]]  # (phone or filler, first PocketSphinx frame, frames)


@dataclass(frozen=True)
class Listener:
    """PocketSphinx with the model folder model (MODEL), decoding jobs lines side by side (all cores when None); the
    phones do not depend on jobs."""
    model: Path
    jobs: int | None = None

    def aligned(self, lines: dict[int, tuple[bytes, str]]) -> dict[int, Segments | None]:
        """Per line (speech at PHONE_RATE, its text), its phones aligned to its words; None for a line with no word
        PocketSphinx can say or that it cannot align. A word the dictionary lacks is said as RULES spell it."""
        known = dictionary(Path(self.model))
        ids, work = sorted(lines), []
        for voice in ids:
            speech, text = lines[voice]
            said = [w for w in words(text) if w in known or spelled(w)]
            work.append((speech, said, [(w, " ".join(spelled(w))) for w in dict.fromkeys(said) if w not in known]))
        return dict(zip(ids, native.module.aligned_phones(str(self.model), work, self._threads()), strict=True))

    def heard(self, lines: dict[int, bytes]) -> dict[int, Segments | None]:
        """Per line (speech at PHONE_RATE), the phones PocketSphinx hears with no words; None when it hears nothing."""
        ids = sorted(lines)
        found = native.module.heard_phones(str(self.model), [lines[v] for v in ids], self._threads())
        return dict(zip(ids, found, strict=True))

    def _threads(self) -> int:
        return self.jobs or os.cpu_count() or 1


def frame_phones(segments: Sequence[tuple[str, int, int]], frames: int) -> bytes:
    """Per lip frame 0..frames-1, three phone numbers (NUMBER, 0 for silence or a noise): the segment sounding at the
    frame's middle, and the segments before and after it, a run of one phone counting as one segment. A frame no
    segment covers is (0, 0, 0)."""
    merged: list[list[int]] = []  # [phone, start, end] in PocketSphinx frames, end exclusive
    for phone, start, length in sorted(segments, key=lambda s: s[1]):
        number = NUMBER.get(phone, 0)
        if merged and merged[-1][0] == number and merged[-1][2] >= start:
            merged[-1][2] = max(merged[-1][2], start + length)
        else:
            merged.append([number, start, start + length])
    out, at = bytearray(), 0
    for t in range(frames):
        # The middle of lip frame t is (t + 1/2) / 30 s, (2t + 1) * 5 / 3 PocketSphinx frames: compared in thirds.
        middle = (2 * t + 1) * 5
        while at + 1 < len(merged) and 3 * merged[at + 1][1] <= middle:
            at += 1
        if not merged or 3 * merged[at][1] > middle or middle >= 3 * merged[at][2]:
            out += b"\0\0\0"
            continue
        out += bytes((merged[at - 1][0] if at else 0, merged[at][0], merged[at + 1][0] if at + 1 < len(merged) else 0))
    return bytes(out)


# What each line says: the text its words are aligned to.

def donor_lines(events: Iterable[bytes], order: str) -> dict[int, str]:
    """A donor's English event lines by line index, from all its event files. The line index of an event voice is its
    donor voice record less the donor's first event record (formats.audio *_EVENT_VOICES), so this is the very line
    the donor plays that clip with. A line two files give different texts, or that is not donor English, is left
    out."""
    found: dict[int, set[bytes]] = {}
    for data in events:
        for index, raw in event_lines(data, order).items():
            found.setdefault(index, set()).add(raw)
    return {index: text for index, raws in found.items()
            if len(raws) == 1 and (text := donor_text(next(iter(raws)))) is not None}


def shown_lines(events: Iterable[tuple[int, bytes]], wanted: set[int], resolver) -> dict[int, str]:
    """The English the game shows for each wanted voice id's stock event line, from (voice base, stock event file)
    pairs: the translation row of the line with its voice id, else of the line alone, as the runtime looks it up
    (runtime event_line). A line no row translates is left out. resolver is a text.DonorResolver."""
    rows = {row["key"]: row for row in json.loads((resources.root() / "data/text/table.json").read_text(encoding="utf-8"))["rows"]}
    found = {}
    for base, data in events:
        for index, raw in sorted(event_lines(data).items()):
            if (voice := base + index) not in wanted or voice in found:
                continue
            keys = [keyed[0] for form in (event_voice_form(raw, voice), raw) if (keyed := stock_key(form))]
            if row := next((rows[key] for key in keys if key in rows), None):
                found[voice] = resolver.resolve_row(row)
    return found
