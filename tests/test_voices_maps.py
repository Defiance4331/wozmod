from wozmod.formats.scripts import Line, Script, Table
from wozmod.voices import dialogue_voice_maps, wo1_voice_map, wo2_voice_map


def _script(*suffixes):
    return Script(Table(()), Table(tuple(Line(b"00Fixture@%04d" % n, True) for n in suffixes)))


def _dreams():
    return ({o: _script() for o in range(40, 68)}, {m: _script() for m in range(2836, 2864)})


def test_story_maps_do_not_cross_mode_boundaries_or_shift_suffix_one():
    stock = {o: _script(1, 9) for o in (0, 39, 40, 71, 72, 131, 132)}
    donors = {900: _script(1, 9)}
    assert wo1_voice_map(stock, donors) == {3144: 1473, 3152: 1481}
    assert wo2_voice_map(stock, donors) == {12408: 1644, 12416: 1652}
    for ordinal in (72, 131):
        assert wo1_voice_map({ordinal: stock[ordinal]}, donors) == {3144: 1473, 3152: 1481}
    for ordinal in (0, 39):
        assert wo2_voice_map({ordinal: stock[ordinal]}, donors) == {12408: 1644, 12416: 1652}
    for ordinal in (40, 71, 132):
        assert wo1_voice_map({ordinal: stock[ordinal]}, donors) == {}
    for ordinal in (40, 72, 131):
        assert wo2_voice_map({ordinal: stock[ordinal]}, donors) == {}


def test_story_maps_do_not_claim_a_suffix_missing_from_the_paired_donor():
    stock = _script(*range(1, 11))
    donor = _script(*range(1, 10))
    assert wo1_voice_map({72: stock}, {579: donor}) == {3144 + i: 1473 + i for i in range(9)}
    assert wo2_voice_map({0: stock}, {2796: donor}) == {12408 + i: 1644 + i for i in range(9)}


def test_quote_fallback_rejects_a_different_or_missing_x360_line():
    stock, donor = _dreams()
    ours = [b"00S1@8000", b"00S2@8001", b"00S3@8002", b"00S4@8003"]
    one = [b"00Fixture A@0007", b"00Fixture B@0008", b"00Fixture C@0009", b"00Fixture D@0010"]
    two = [b"12Fixture A@0011", b"00Fixture other@0012", b"", b"00Fixture D"]
    wo1, wo2 = dialogue_voice_maps(stock, {}, donor, [(ours, one)], [(ours, two)])
    assert {k: v for k, v in wo1.items() if k >= 20000} == {20407: 1479, 20410: 1482}
    assert {k: v for k, v in wo2.items() if k >= 20000} == {20407: 1654, 20408: 1655}


def test_dream_slot_cannot_replace_a_story_clip_with_a_different_suffix():
    stock, donor = _dreams()
    stock[0], donor[2796] = _script(100), _script(100)
    stock[40], donor[2836] = _script(100, 8000), _script(7, 8)
    _, wo2 = dialogue_voice_maps(stock, {}, donor, [], [])
    assert wo2[12507] == 1743
    assert wo2[20407] == 1651


def test_dialogue_maps_do_not_drop_fixed_event_select_or_narration_runs():
    stock, donor = _dreams()
    one, two = dialogue_voice_maps(stock, {}, donor, [], [])
    expected_one = {2292 + i: 957 + i for i in range(511)}
    expected_one |= {96 + i: 80 + i for i in range(209)}
    expected_one |= {306 + i: 289 + i for i in range(40)}
    expected_one |= {592 + i: 329 + i for i in range(628)}
    expected_two = {2803 + i: 1398 + i for i in range(246)}
    expected_two |= {i: i for i in range(92)}
    expected_two |= {346 + i: 92 + i for i in range(234)}
    expected_two |= {1220 + i: 326 + i for i in range(1072)}
    assert (one, two) == (expected_one, expected_two)
