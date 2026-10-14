"""The runtime lookup table, checked against many random row sets (hypothesis): hand-picked rows rarely
collide at the table's last slot."""

from __future__ import annotations

import struct

from hypothesis import given, settings, strategies as st

from wozmod.runtime import english_runtime as runtime


def _fnv1a(value: bytes) -> int:
    h = 0x811C9DC5
    for b in value:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def _lookup(blob: bytes, key: bytes) -> bytes | None:
    """The runtime's probe: start at hash & mask, step one slot (wrapping) until the key or an empty slot."""
    mask = struct.unpack_from("<I", blob)[0]
    h = _fnv1a(key)
    i = h & mask
    for _ in range(mask + 1):  # a table with no empty slot would loop forever in the game; here it fails
        slot_hash, jp, en = struct.unpack_from("<3I", blob, 8 + 12 * i)
        if not jp:
            return None
        if slot_hash == h and blob[jp:blob.index(b"\0", jp)] == key:
            return blob[en:blob.index(b"\0", en)]
        i = (i + 1) & mask
    raise AssertionError("probe visited every slot without finding the key or an empty slot")


KEYS = st.binary(min_size=1, max_size=6).filter(lambda b: b"\0" not in b)


@given(st.dictionaries(KEYS, st.binary(max_size=6).filter(lambda b: b"\0" not in b), max_size=40), KEYS)
@settings(max_examples=300)
def test_runtime_table_finds_every_row_and_nothing_else(rows: dict[bytes, bytes], other: bytes) -> None:
    # Bug: a probe that stops at the last slot instead of wrapping, or a table sized too small to hold an
    # empty slot, loses a row or never ends; the game would show that line in Japanese.
    blob = runtime.table_blob(rows)
    assert {key: _lookup(blob, key) for key in rows} == rows
    assert other in rows or _lookup(blob, other) is None
