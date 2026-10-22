"""A sentence that names the game's arguments in another order than the Japanese: the text build numbers its
placeholders (wozmod.text.build) and the runtime moves the arguments to match (runtime/format_order.h)."""

from __future__ import annotations

import ctypes

import pytest

from wozmod.text.build import table_pairs


@pytest.fixture(scope="module")
def order(c_library):
    lib = c_library("order", r'''
#include "format_order.h"
int order(const char *in, char *out, unsigned int size, unsigned int *args, unsigned int count) {
    return format_order(in, out, size, args, count); }
''')
    return lib.order


def printed(order, stock: bytes, row: dict, args: list[int]) -> bytes:
    """The line the game prints: the table's English for ``stock``, through the runtime's sprintf hook, then a
    sprintf that reads its arguments in turn, given ``args`` in the order the game passes them."""
    english = table_pairs([row])[stock]
    out, passed = ctypes.create_string_buffer(512), (ctypes.c_uint * 9)(*args)
    form = out.value if order(english, out, len(out), passed, 9) else english
    return form % tuple(passed[:len(args)])


@pytest.mark.parametrize(("stock", "english", "row_order", "args", "line"), [
    ("Within %d min, %d officers", "Defeat %d officers in %d minutes.", [2, 1], [12, 3],
     b"Defeat 3 officers in 12 minutes."),
    ("Keep %d%% life, %d KOs", "Defeat %d while keeping %d%% life.", [2, 1], [50, 100],
     b"Defeat 100 while keeping 50% life."),
    ("Keep %d%% life, %d KOs", "Keep %d%% life and defeat %d.", [1, 2], [50, 100], b"Keep 50% life and defeat 100."),
    ("%d, %02d, %d", "c%d a%d b%02d", [3, 1, 2], [1, 2, 3], b"c3 a1 b02"),
])
def test_the_game_s_arguments_land_where_the_english_names_them(order, stock, english, row_order, args, line) -> None:
    # Bug: English that names the numbers in another order than the Japanese prints them swapped ("defeat 12
    # officers in 3 minutes"), the runtime moves them the wrong way, or a %% next to a placeholder is lost.
    row = {"ja": stock, "en": english, "order": row_order}
    assert printed(order, stock.encode(), row, args) == line


def test_a_numbered_placeholder_the_game_does_not_pass_leaves_the_format_alone(order) -> None:
    # Bug: a number past the arguments reads beyond them; a format without numbers is rewritten anyway.
    out, passed = ctypes.create_string_buffer(64), (ctypes.c_uint * 2)(7, 8)
    assert order(b"%3$d %1$d", out, 64, passed, 2) == 0 and list(passed) == [7, 8]
    assert order(b"plain %d %d", out, 64, passed, 2) == 0 and list(passed) == [7, 8]
