"""table.bin, the English text table the runtime looks text up in: its CP932 text encoding and its layout.

The installer's text step writes it and the DLL reads it; the build steps import this module, not english_runtime (the
DLL build), so that a runtime change keeps their keys (wozmod.install.step_keys)."""

from __future__ import annotations

import struct

from .latin_font import SURROGATE_CODE, SURROGATE_LETTERS

# Each letter CP932 lacks travels as the character at its surrogate code (latin_font).
SURROGATES = str.maketrans({letter: (SURROGATE_CODE + i).to_bytes(2, "big").decode("cp932")
                            for i, letter in enumerate(SURROGATE_LETTERS)})
_UNSURROGATES = {ord(v): k for k, v in SURROGATES.items()}


def encode_english(text: str) -> bytes:
    """CP932 bytes for English text, with macron vowels on their surrogate glyphs."""
    return text.translate(SURROGATES).encode("cp932")


def decode_english(raw: bytes, errors: str = "strict") -> str:
    """The text ``encode_english`` gave ``raw`` for."""
    return raw.decode("cp932", errors).translate(_UNSURROGATES)


def table_blob(pairs: dict[bytes, bytes], names: list[bytes | None] = (), overridden: list[int] = ()) -> bytes:
    """Pack the runtime lookup table, the part slots' English offsets (build.PART_TABLES in order) and the slots a
    per-index override gives, which the runtime serves by the slot's stock pointer."""
    size = 1
    while size < 2 * len(pairs) + 2:
        size *= 2
    header = 8 + 12 * size
    pool, offsets = bytearray(), {}

    def put(value: bytes) -> int:
        if b"\0" in value:
            raise ValueError("embedded NUL")
        if value not in offsets:
            offsets[value] = header + len(pool)
            pool.extend(value + b"\0")
        return offsets[value]

    slots = [(0, 0, 0)] * size
    for jp, en in pairs.items():
        if not jp:
            raise ValueError("empty key")
        h = fnv1a(jp)
        i = h & (size - 1)
        while slots[i][1]:
            i = (i + 1) & (size - 1)
        slots[i] = (h, put(jp), put(en))
    name_offsets = [0 if en is None else put(en) for en in names]
    pool.extend(bytes(-len(pool) % 4))
    return (struct.pack("<II", size - 1, header + len(pool))
            + b"".join(struct.pack("<3I", *s) for s in slots) + bytes(pool)
            + struct.pack(f"<{len(names) + 1}I", len(names), *name_offsets)
            + struct.pack(f"<{len(overridden) + 1}I", len(overridden), *overridden))

def fnv1a(value: bytes) -> int:
    h = 2166136261
    for b in value:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def table_lookup(blob: bytes, key: bytes) -> bytes | None:
    """The English table_blob holds for key, found as enrt_translate finds it: start at hash & mask and step one slot
    (wrapping) until the key or an empty slot."""
    mask = struct.unpack_from("<I", blob)[0]
    h = fnv1a(key)
    i = h & mask
    for _ in range(mask + 1):  # a table with no empty slot would loop forever in the game; here it fails
        slot_hash, jp, en = struct.unpack_from("<3I", blob, 8 + 12 * i)
        if not jp:
            return None
        if slot_hash == h and blob[jp:blob.index(b"\0", jp)] == key:
            return blob[en:blob.index(b"\0", en)]
        i = (i + 1) & mask
    raise ValueError("the table has no empty slot")
