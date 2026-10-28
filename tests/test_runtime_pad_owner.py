"""Who owns a pad, and what happens to their buttons when that pad leaves (runtime/pad_owner.h)."""

from __future__ import annotations

import ctypes
import struct

import pytest

NONE = 10


@pytest.fixture(scope="module")
def owner(c_library):
    lib = c_library("pad_owner", r'''
#include "pad_owner.h"
#include <string.h>
int removed(unsigned char *map, unsigned int *joy, unsigned int index, struct pad_park *park)
{
    return pad_index_removed(map, joy, index, park);
}
struct pad_claim claim(unsigned char *map, unsigned int joy, unsigned int other, unsigned int acting, int own_live,
                       struct pad_park *park)
{
    return pad_claim(map, joy, other, acting, own_live, park);
}
/* What the writer sees (out0, out1, outjoy), then the memory after the save job put the park back. */
void written(unsigned char *m0, unsigned char *m1, unsigned int *joy, struct pad_park *park, unsigned char *out0,
             unsigned char *out1, unsigned int *outjoy)
{
    unsigned char *maps[2] = {m0, m1};
    struct pad_undo undo;
    pad_file_begin(maps, joy, park, &undo);
    memcpy(out0, m0, 21 * 0x28);
    memcpy(out1, m1, 21 * 0x28);
    outjoy[0] = joy[0], outjoy[1] = joy[1];
    pad_file_end(maps, joy, &undo);
}
''')
    class Park(ctypes.Structure):
        _fields_ = [("mask", ctypes.c_uint), ("pad", ctypes.c_uint)]

    class Claim(ctypes.Structure):
        _fields_ = [("joy", ctypes.c_uint), ("moved", ctypes.c_uint), ("restored", ctypes.c_uint),
                    ("defaults", ctypes.c_int)]

    lib.removed.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_uint, ctypes.POINTER(Park)]
    lib.removed.restype = ctypes.c_int
    lib.claim.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int,
                          ctypes.POINTER(Park)]
    lib.claim.restype = Claim
    lib.written.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(Park),
                            ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)]
    lib.Claim, lib.Park = Claim, Park
    return lib


def input_map(pad_bindings: dict[int, tuple[int, int, int, int]]) -> ctypes.Array:
    """21 actions of 0x28 bytes; each pad binding (device, isAxis, direction, index) at +0x18."""
    data = bytearray(21 * 0x28)
    for action, (device, is_axis, direction, index) in pad_bindings.items():
        struct.pack_into("<IBxxxII", data, action * 0x28 + 0x18, device, is_axis, direction, index)
    return ctypes.create_string_buffer(bytes(data), len(data))


def binding(buffer: ctypes.Array, action: int) -> tuple[int, int, int, int]:
    device, is_axis, direction, index = struct.unpack_from("<IBxxxII", buffer.raw, action * 0x28 + 0x18)
    return device, is_axis, direction, index


def saved_on(device: int) -> dict[int, tuple[int, int, int, int]]:
    """A layout with a stick and a chosen button, plus the two camera actions the stock defaults leave empty."""
    fields = {action: (device, 0, 0, action % 12) for action in range(17)}
    fields[0] = (device, 1, 1, 1)
    fields[9] = (device, 0, 0, 4)
    fields[17] = (device, 1, 1, 4)
    fields[18] = (device, 1, 0, 4)
    fields[19] = (NONE, 0, 0, 0)
    fields[20] = (NONE, 0, 0, 0)
    return fields


def remove(owner, buffer, joy: int, index: int, park=None):
    """(was it this player's pad, the player's joy after, the park record)."""
    j, park = ctypes.c_uint(joy), park or owner.Park()
    gone = owner.removed(buffer, ctypes.byref(j), index, ctypes.byref(park))
    return gone, j.value, park


def claim(owner, buffer, joy: int, other: int, acting: int, own_live: int, park):
    """The claim and the park mask left. `park` is a record from remove(), or a bare mask."""
    park = park if isinstance(park, owner.Park) else owner.Park(park, 0)
    return owner.claim(buffer, joy, other, acting, own_live, ctypes.byref(park)), park.mask


def test_removing_a_pad_parks_exactly_its_buttons_and_a_pad_that_stayed_keeps_them(owner) -> None:
    """Bugs: a binding left on the removed index reads whatever slid into that hole, so the press does nothing or
    hits the wrong pad; a later pad's device is not moved down, so a player who is still connected names a pad that
    is no longer there; the parked set misses an action or includes one that was never on the removed pad (the
    unbound camera actions, another pad's action). The button index itself must not change."""
    buffer = input_map({**saved_on(0), 10: (2, 0, 0, 7)})
    gone, joy, parked = remove(owner, buffer, 0, 0)
    assert (gone, joy) == (1, NONE)
    assert parked.mask == sum(1 << a for a in range(19) if a != 10)
    assert binding(buffer, 9) == (NONE, 0, 0, 4)
    assert binding(buffer, 0) == (NONE, 1, 1, 1)
    assert binding(buffer, 10) == (1, 0, 0, 7)
    assert binding(buffer, 19) == (NONE, 0, 0, 0)

    stayed = input_map({**saved_on(2), 10: (0, 0, 0, 7)})
    gone, joy, parked = remove(owner, stayed, 2, 0)
    assert (gone, joy, parked.mask) == (0, 1, 1 << 10)
    assert binding(stayed, 9) == (1, 0, 0, 4)
    assert binding(stayed, 10) == (NONE, 0, 0, 7)


def test_input_on_a_new_pad_puts_back_the_parked_buttons_and_only_those(owner) -> None:
    """Bugs: the player's pad is chosen once, so after it leaves a press on the pad that replaced it does nothing; a
    device-10 binding that no removal parked is moved onto the pad too, because its fields look like a layout. Two
    device-10 bindings with identical fields differ only in whether a removal parked them, so only that decides: an
    unparked one holds stack residue as often as a choice (the stock map never writes its fields), and putting a
    residue index on the pad makes the game read off the end of its buttons."""
    buffer = input_map({**{a: (NONE, 0, 0, 0) for a in range(21)}, 0: (0, 1, 1, 1), 9: (0, 0, 0, 4), 3: (NONE, 0, 0, 4),
                        5: (NONE, 0, 7694012, 64)})
    _, joy, parked = remove(owner, buffer, 0, 0)
    assert parked.mask == 1 | 1 << 9
    got, left = claim(owner, buffer, joy, NONE, 0, 0, parked)
    assert (got.joy, got.moved, got.defaults, got.restored) == (0, 0, 0, 2)
    assert left == 0
    assert binding(buffer, 0) == (0, 1, 1, 1)
    assert binding(buffer, 9) == (0, 0, 0, 4)
    assert binding(buffer, 3) == (NONE, 0, 0, 4)
    assert binding(buffer, 5) == (NONE, 0, 7694012, 64)


def test_a_map_nothing_parked_gets_the_defaults_and_none_of_its_fields_go_on_the_pad(owner) -> None:
    """Bugs: a map the game built itself (device 10 everywhere, residue in the direction and the index, joy 10) is
    restored onto the pad that was pressed instead of asking for the defaults; the same for a map whose choices
    are real but were never parked (the player's own clearing). Nothing is written onto the map by the claim."""
    for fields in ({action: (NONE, 0, 7694012, 64) for action in range(21)}, saved_on(NONE)):
        buffer = input_map(fields)
        before = buffer.raw
        got, _ = claim(owner, buffer, NONE, NONE, 1, 0, 0)
        assert (got.joy, got.defaults, got.restored, got.moved) == (1, 1, 0, 0)
        assert buffer.raw == before


def test_a_parked_action_the_player_bound_again_is_left_alone_and_the_park_is_used_up(owner) -> None:
    """Bugs: a put-back overwrites a pad binding the player made while the pad was away; the parked set outlives
    the claim, so the next pad that is removed and replaced puts back actions from the first round."""
    buffer = input_map(saved_on(0))
    _, joy, parked = remove(owner, buffer, 0, 0)
    struct.pack_into("<I", buffer, 9 * 0x28 + 0x18, 1)  # action 9 bound by hand to pad 1 while joy is 10
    got, left = claim(owner, buffer, joy, NONE, 2, 0, parked)
    assert binding(buffer, 9)[0] == 1 and binding(buffer, 0)[0] == 2
    assert (got.restored, left) == (18, 0)  # every parked action but 9


def test_the_other_players_pad_is_never_taken(owner) -> None:
    """Bug: player 1's pad leaving lets the next press steal player 2's pad, so one controller starts driving both."""
    mine = input_map(saved_on(0))
    _, joy, parked = remove(owner, mine, 0, 0)
    got, left = claim(owner, mine, joy, 0, 0, 0, parked)
    assert (got.joy, got.moved, got.restored, got.defaults) == (NONE, 0, 0, 0)
    assert left == parked.mask and binding(mine, 9) == (NONE, 0, 0, 4)
    theirs = input_map(saved_on(0))
    untouched, _ = claim(owner, theirs, 0, NONE, 1, 1, 0)
    assert untouched.joy == 0 and binding(theirs, 9) == (0, 0, 0, 4)


def test_a_live_pad_is_not_stolen_by_another_pads_press(owner) -> None:
    """Bug: activity on any pad steals player 1, so the other controller's press rebinds them."""
    buffer = input_map(saved_on(0))
    got, _ = claim(owner, buffer, 0, NONE, 1, 1, 0)
    assert (got.joy, got.moved, got.restored, got.defaults) == (0, 0, 0, 0)
    assert binding(buffer, 9) == (0, 0, 0, 4)


def test_replug_and_a_swap_keep_the_same_button(owner) -> None:
    """Bug: an unplug and replug, or a swap to a different controller, is a special case that leaves the player on
    the pad that left. Both are the same claim: the pad is gone, and the next one that is used keeps the button."""
    for acting in (0, 1):  # the same index again, then a different pad
        buffer = input_map(saved_on(0))
        _, joy, parked = remove(owner, buffer, 0, 0)
        got, _ = claim(owner, buffer, joy, NONE, acting, 0, parked)
        assert (got.joy, got.defaults) == (acting, 0)
        assert binding(buffer, 9) == (acting, 0, 0, 4)


def write_file(owner, maps, joys, parks):
    """What the game's map writer sees for the two players (maps as bytes, joys), and the memory afterwards."""
    j = (ctypes.c_uint * 2)(*joys)
    p = (owner.Park * 2)(*parks)
    out = [ctypes.create_string_buffer(21 * 0x28) for _ in range(2)]
    outjoy = (ctypes.c_uint * 2)()
    owner.written(maps[0], maps[1], j, p, out[0], out[1], outjoy)
    return out, list(outjoy), list(j)


def test_a_save_while_a_pad_is_gone_writes_the_player_on_the_pad_they_had(owner) -> None:
    """Bug: the park is only in memory, so a save while the pad is away writes joy 10 and every binding at device 10,
    and the next boot has nothing to put back (the player gets the defaults). The file must read as if the pad had
    stayed, the memory must be parked again afterwards, a binding no removal parked must stay at device 10 in the
    file, and the next boot's claim must carry the file's layout onto whichever pad arrives."""
    layout = {**{a: (NONE, 0, 0, 0) for a in range(21)}, **saved_on(2), 11: (NONE, 0, 7694012, 64)}
    mine, theirs = input_map(layout), input_map(saved_on(1))
    _, joy, park = remove(owner, mine, 2, 2)
    assert (joy, park.pad) == (NONE, 2)
    out, outjoy, after = write_file(owner, (mine, theirs), (joy, 1), (park, owner.Park()))
    assert outjoy == [2, 1]
    assert [binding(out[0], a)[0] for a in range(21)] == [2 if a in range(19) and a != 11 else NONE for a in range(21)]
    assert binding(out[0], 0) == (2, 1, 1, 1) and binding(out[0], 11) == (NONE, 0, 7694012, 64)
    assert after == [NONE, 1]
    assert binding(mine, 9) == (NONE, 0, 0, 4) and park.mask == sum(1 << a for a in range(19) if a != 11)
    assert out[1].raw == theirs.raw
    # next boot: joy 2 is not a pad here, the first pad that acts (index 0) takes the layout
    boot, _ = out[0], None
    got, _ = claim(owner, boot, 2, NONE, 0, 0, 0)
    assert (got.joy, got.moved, got.defaults) == (0, 18, 0)
    assert binding(boot, 0) == (0, 1, 1, 1) and binding(boot, 11) == (NONE, 0, 7694012, 64)


def test_a_save_with_both_players_parked_keeps_each_on_their_own_pad(owner) -> None:
    """Bug: both parked players are written on the same pad index (each removal shifts the other down, so both were
    on index 0 when they left), and the next boot has two players on one pad. Player 2 yields to player 1, and a
    parked player yields to the other's live pad."""
    one, two = input_map(saved_on(0)), input_map(saved_on(1))
    _, j0, park0 = remove(owner, one, 0, 0)
    _, shifted, _ = remove(owner, two, 1, 0)  # pad 0 left: player 2's pad 1 became pad 0
    _, j1, park1 = remove(owner, two, shifted, 0)  # then that pad left too
    assert (j0, j1, park0.pad, park1.pad) == (NONE, NONE, 0, 0)
    out, outjoy, after = write_file(owner, (one, two), (j0, j1), (park0, park1))
    assert outjoy == [0, 1] and after == [NONE, NONE]
    assert binding(out[0], 9)[0] == 0 and binding(out[1], 9)[0] == 1
    assert binding(one, 9)[0] == NONE and binding(two, 9)[0] == NONE

    live, parked = input_map(saved_on(0)), input_map(saved_on(1))
    _, joy, park = remove(owner, parked, 1, 1)
    park.pad = 0  # it was the pad that is now index 0 for the other player
    out, outjoy, _ = write_file(owner, (live, parked), (0, joy), (owner.Park(), park))
    assert outjoy == [0, 1] and binding(out[1], 9)[0] == 1


def test_a_save_after_the_park_is_used_up_writes_the_live_map_untouched(owner) -> None:
    """Bugs: a map rebuilt while no pad was there (the record cleared, joy 10) is written on pad 0; the saved file is
    rewritten for a player who has a pad again (their live bindings replaced by an old
    park's pad index), or a player who never lost a pad is changed."""
    buffer, other = input_map(saved_on(0)), input_map(saved_on(1))
    _, joy, park = remove(owner, buffer, 0, 0)
    got, _ = claim(owner, buffer, joy, NONE, 3, 0, park)
    assert got.joy == 3
    out, outjoy, after = write_file(owner, (buffer, other), (3, 1), (park, owner.Park()))
    assert outjoy == [3, 1] == after and out[0].raw == buffer.raw and out[1].raw == other.raw
    # the map was rebuilt while no pad was there (hook 121 clears the record): joy 10 stays 10 in the file
    rebuilt = input_map({a: (NONE, 0, 7694012, 64) for a in range(21)})
    out, outjoy, _ = write_file(owner, (rebuilt, other), (NONE, 1), (owner.Park(), owner.Park()))
    assert outjoy == [NONE, 1] and out[0].raw == rebuilt.raw
