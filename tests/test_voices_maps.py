from dataclasses import replace


from wozmod.formats.scripts import Line, Script, Table
from wozmod.text.stock import stock_key
from wozmod.voices import dialogue_voice_maps, psp_voice_map, wo1_voice_map, wo2_voice_map
from wozmod.voices.maps import shown_lines


def _speaks(lines: tuple[Line, ...], flags: list[int]) -> Script:
    """A stage script whose event i speaks line i, by person i, with bank flag flags[i]."""
    events = tuple(((0x26, i, i, 0, 0, 0, 0, flag),) for i, flag in enumerate(flags))
    return Script(Table(()), Table(lines), {i: {flag} for i, flag in enumerate(flags)}, events)


def _script(*suffixes):
    return _speaks(tuple(Line(b"00Fixture@%04d" % n, True) for n in suffixes), [0] * len(suffixes))


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


def test_story_line_takes_the_voice_of_the_donor_line_its_own_row_shows():
    # WO1 X360 stage 107 says two of one officer's sentences in the other order, and their rows pair them by meaning.
    # Bugs: the voice joins by suffix alone (it says the other sentence); a row two spoken lines share moves a voice;
    # a row pointing into another donor script moves a voice; a row pointing at a donor line with the same words as
    # the line's own (a repeated call, perhaps by another officer) takes that take instead of the line's own.
    stock = Script(Table(()), Table(tuple(Line(value, True) for value in (
        b"00S1@0001", b"00S2@0002", b"00S3@0003", b"00S3@0004", b"00S5@0005", b"00S6@0006"))))
    donor = Script(Table(()), Table(tuple(Line(b"00Fixture %s@%04d" % (word, n), True) for n, word in (
        (1, b"A"), (2, b"B"), (3, b"C"), (4, b"D"), (5, b"E"), (6, b"A")))))
    for mapping, ordinal, ens, source, base, record in ((wo1_voice_map, 72, 123, "wo1-x360", 3144, 1473),
                                                        (wo2_voice_map, 0, 2816, "wo2-x360", 12408, 1644)):
        where = [source, "ens", ens - 2693 if source == "wo2-x360" else ens, "dialog"]
        rows = {stock_key(text)[0]: {"ref": where + [line]}
                for text, line in ((b"S1", 1), (b"S2", 0), (b"S3", 4), (b"S6", 0))}
        rows[stock_key(b"S5")[0]] = {"ref": [source, "ens", 99, "dialog", 0]}
        moz = {ordinal: stock}
        assert mapping(moz, {ens: donor}, shown_lines(moz, rows)) == {
            base: record + 1, base + 1: record, **{base + i: record + i for i in range(2, 6)}}


def test_quote_fallback_rejects_a_different_or_missing_x360_line():
    stock, donor = _dreams()
    ours = [b"00S1@8000", b"00S2@8001", b"00S3@8002", b"00S4@8003"]
    one = [b"00Fixture A@0007", b"00Fixture B@0008", b"00Fixture C@0009", b"00Fixture D@0010"]
    two = [b"12Fixture A@0011", b"00Fixture other@0012", b"", b"00Fixture D"]
    wo1, wo2 = dialogue_voice_maps(stock, {}, donor, [(ours, one)], [(ours, two)])
    assert {k: v for k, v in wo1.items() if k in range(20407, 20411)} == {20407: 1479, 20410: 1482}
    assert {k: v for k, v in wo2.items() if k in range(20407, 20411)} == {20407: 1654, 20408: 1655}


def test_a_dream_line_takes_the_take_its_speak_commands_counterpart_speaks():
    # Bug: Dream lines pair with donor lines by place in the table, so the line stock Dream stage 40 inserts (suffix
    # 101, spoken by a person the donor lacks) takes the next donor line's take, and every later line slides one take
    # on. Ids by hand: suffix less one on 0x3078, record suffix less one on 1644. Stages 41 and 42 show persons 0 and
    # 1 in both games, so the person map learns them there.
    stock, donor = _dreams()
    stock[41], stock[42], donor[2837], donor[2838] = _script(200, 201), _script(300, 301), _script(20, 21), _script(30, 31)
    stock[40] = _speaks(tuple(Line(b"00Fixture@%04d" % n, True) for n in (100, 101, 102)), [0, 0, 0])
    stock[40] = replace(stock[40], events=(((0x26, 0, 0, 0, 0, 0, 0, 0),), ((0x26, 9, 1, 0, 0, 0, 0, 0),),
                                           ((0x26, 1, 2, 0, 0, 0, 0, 0),)))
    donor[2836] = _script(7, 8)
    _, wo2 = dialogue_voice_maps(stock, {}, donor, [], [])
    assert {i: wo2.get(i) for i in (12507, 12508, 12509)} == {12507: 1650, 12508: None, 12509: 1651}


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
    expected_two = {2803 + i: 1398 + i for i in range(241)}  # WO2 X360 0x8219F8D8 voices event lines below 0xF1
    expected_two |= {i: i for i in range(92)} | {0x5875: 0x28F4, 0x5883: 0x2902}
    expected_two |= {346 + i: 92 + i for i in range(234)}
    expected_two |= {1220 + i: 326 + i for i in range(1072)}
    assert (one, two) == (expected_one, expected_two)


def test_psp_map_joins_z_dream_lines_through_their_speak_commands_voiced_quote_slots_and_new_officers():
    # Bugs: the PSP clip base is off by one; a Z Dream line pairs with a PSP line by place, so the line stock stage
    # 139 inserts (suffix 101, spoken by person 9, whom the PSP lacks) takes the next PSP line's take and the line
    # after it slides one take on; a quote slot the PSP voices by an SV code (which plays nothing) is taken for a
    # clip; Benkei and Sanzang's select voices are dropped. Stages 132 and 133 show persons 0 and 1 in both games, so
    # the person map learns them there. Ids by hand: suffix less one on 0x3078, PSP record suffix less one on 1419.
    stock = {132: _script(200, 201), 133: _script(300, 301)}
    psp = {132: _script(20, 21), 133: _script(30, 31), 139: _script(7, 8)}
    stock[139] = replace(_script(100, 101, 102), events=(((0x26, 0, 0, 0, 0, 0, 0, 0),), ((0x26, 9, 1, 0, 0, 0, 0, 0),),
                                                         ((0x26, 1, 2, 0, 0, 0, 0, 0),)))
    quotes = [([b"00A@7000", b"00B@7001", b"00C@7002"], [b"00a@0201", b"00b@SV094_02", b""])]

    expected = {12607: 1438, 12608: 1439, 12707: 1448, 12708: 1449, 12507: 1425, 12509: 1426}
    expected |= {12408 + 6999: 1419 + 200, 94: 94, 95: 95}
    assert psp_voice_map(stock, psp, quotes) == expected


def test_a_flagged_line_keys_the_other_banks_id_and_leaves_the_unflagged_one_free():
    # Bug: a Dream or Z Dream slot keys its line's id from the stage's bank alone, ignoring the speak command's flag
    # (which picks the other bank). Stock stage 143 line 7 is flagged and plays 0xC48 + 4447; stage 66 line 42 has
    # the same suffix, no flag and no donor take, and plays 0x3078 + 4447: the flag-blind slot gave stage 143's PSP
    # clip to that id, so the English bank played another line over stage 66's subtitle. Ids by hand from the bases.
    def spoken(*lines):
        return _speaks(tuple(Line(value, True) for value, _ in lines), [flag for _, flag in lines])

    flagged, plain = spoken((b"00S1@4448", 1), (b"00S2@0005", 0)), spoken((b"00S3@4448", 0))
    flagged_id, plain_id = 0xC48 + 4447, 0x3078 + 4447
    psp = {143: _script(7, 6)}
    assert psp_voice_map({143: flagged, 66: plain}, psp, []) == {flagged_id: 1419 + 6, 0x3078 + 4: 1419 + 5, 94: 94, 95: 95}
    stock, donor = _dreams()
    stock[40], stock[66], donor[2836], donor[2862] = flagged, plain, _script(7, 6), Script(Table(()), Table((Line(b"00T", True),)))
    _, wo2 = dialogue_voice_maps(stock, {}, donor, [], [])
    assert (wo2.get(flagged_id), wo2.get(0x3078 + 4), plain_id in wo2) == (1644 + 6, 1644 + 5, False)
