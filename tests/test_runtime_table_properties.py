"""The runtime lookup table, checked against many random row sets (hypothesis): hand-picked rows rarely
collide at the table's last slot."""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from wozmod.runtime import text_table as runtime


KEYS = st.binary(min_size=1, max_size=6).filter(lambda b: b"\0" not in b)


@given(st.dictionaries(KEYS, st.binary(max_size=6).filter(lambda b: b"\0" not in b), max_size=40), KEYS)
@settings(max_examples=300)
def test_runtime_table_finds_every_row_and_nothing_else(rows: dict[bytes, bytes], other: bytes) -> None:
    # Bug: a probe that stops at the last slot instead of wrapping, or a table sized too small to hold an
    # empty slot, loses a row or never ends; the game would show that line in Japanese.
    blob = runtime.table_blob(rows)
    assert {key: runtime.table_lookup(blob, key) for key in rows} == rows
    assert other in rows or runtime.table_lookup(blob, other) is None
