import os
import re
import statistics
import struct
from pathlib import Path

import pytest

from wozmod.formats.scripts import Line, Script, Table
from wozmod.text.stock import stock_key
from wozmod.voices import dialogue_voice_maps, psp_voice_map, wo1_voice_map, wo2_voice_map
from wozmod.voices.maps import shown_lines


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


def test_psp_map_joins_dream_lines_past_stage_139_insertion_voiced_quote_slots_and_new_officers():
    # Bug: the PSP clip base is off by one, the 12 lines stock stage 139 inserts shift every later line onto another
    # PSP clip, a quote slot the PSP voices by an SV code (no bank holds those) is taken for a clip, or Benkei and
    # Sanzang's select voices are dropped.
    def stage(notices, *suffixes):
        return Script(Table((Line(b"32Fixture", False),) * notices), _script(*suffixes).dialog)

    stock = {o: stage(56 if o == 142 else 0, 5000 + o) for o in range(132, 144)}
    psp = {o: stage(49 if o == 142 else 0, 100 + o) for o in range(132, 144)}
    stock[139], psp[139] = stage(0, *range(6000, 6076)), stage(0, *range(1, 65))
    quotes = [([b"00A@7000", b"00B@7001", b"00C@7002"], [b"00a@0201", b"00b@SV094_02", b""])]

    expected = {12408 + 4999 + o: 1419 + 99 + o for o in range(132, 144) if o != 139}
    expected |= {12408 + 5999 + i: 1419 + i for i in range(56)}
    expected |= {12408 + 6067 + i: 1419 + 56 + i for i in range(8)}
    expected |= {12408 + 6999: 1419 + 200, 94: 94, 95: 95}
    assert psp_voice_map(stock, psp, quotes) == expected


def test_a_flagged_line_keys_the_other_banks_id_and_leaves_the_unflagged_one_free():
    # Bug: a Dream or Z Dream slot keys its line's id from the stage's bank alone, ignoring the speak command's flag
    # (which picks the other bank). Stock stage 143 line 7 is flagged and plays 0xC48 + 4447; stage 66 line 42 has
    # the same suffix, no flag and no donor take, and plays 0x3078 + 4447: the flag-blind slot gave stage 143's PSP
    # clip to that id, so the English bank played another line over stage 66's subtitle. Ids by hand from the bases.
    def spoken(*lines):
        return Script(Table(()), Table(tuple(Line(value, True) for value, _ in lines)),
                      {i: {flag} for i, (_, flag) in enumerate(lines)})

    flagged, plain = spoken((b"00S1@4448", 1), (b"00S2@0005", 0)), spoken((b"00S3@4448", 0))
    flagged_id, plain_id = 0xC48 + 4447, 0x3078 + 4447
    psp = {143: _script(7, 6)}
    assert psp_voice_map({143: flagged, 66: plain}, psp, []) == {flagged_id: 1419 + 6, 0x3078 + 4: 1419 + 5, 94: 94, 95: 95}
    stock, donor = _dreams()
    stock[40], stock[66], donor[2836], donor[2862] = flagged, plain, _script(7, 6), Script(Table(()), Table((Line(b"00T", True),)))
    _, wo2 = dialogue_voice_maps(stock, {}, donor, [], [])
    assert (wo2.get(flagged_id), wo2.get(0x3078 + 4), plain_id in wo2) == (1644 + 6, 1644 + 5, False)


@pytest.mark.skipif(not (os.environ.get("WOZMOD_GAME") and os.environ.get("WOZMOD_DONORS")
                         and os.environ.get("WOZMOD_WO2_PSP_ISO")),
                    reason="set WOZMOD_GAME, WOZMOD_DONORS and WOZMOD_WO2_PSP_ISO")
@pytest.mark.slow_first
def test_every_psp_voice_group_plays_the_stock_lines_own_japanese_take() -> None:
    # Bug: a wrong PSP clip base, or a Z Dream stage or quote slot paired one line off, plays another line's English.
    # Oracle: the PSP's Japanese bank holds the same takes as the stock one at the same members, so within a group
    # (a Z Dream stage, a quote slot) the stock length less the PSP length is one constant (the stock's padding).
    # Measured: each group agrees for 60-93 % of its lines; shifted by one line, at most 25 %.
    from wozmod.discs.iso9660 import Iso9660
    from wozmod.donors import DonorsFolder
    from wozmod.formats.audio import PSP_SELECT_VOICES, parse_kovs_record, quote_voice, stage_line_voices
    from wozmod.formats.linkdata import archive, decoded_member
    from wozmod.formats.scripts import quote_tables
    from wozmod.text.script_alignment import QUOTES, moz_scripts
    from wozmod.voices.build import PSP_TEXT, psp_text_voice_map
    from wozmod.voices.maps import UNSPOKEN

    game = Path(os.environ["WOZMOD_GAME"])
    moz, linkdata1 = moz_scripts(game / "LINKDATA2"), archive(game / "LINKDATA1")
    quotes = linkdata1[QUOTES[0]]
    mapping = psp_text_voice_map(moz, quotes, DonorsFolder(Path(os.environ["WOZMOD_DONORS"])).folder("wo2-psp") / PSP_TEXT)
    groups: dict[str, list[int]] = {}
    for ordinal in range(132, 144):
        for ids in stage_line_voices(ordinal, moz[ordinal], UNSPOKEN).values():
            groups.setdefault(f"stage {ordinal}", []).extend(i for i in ids if i in mapping)
    for table in quote_tables(quotes):
        for slot, line in enumerate(table):
            if (found := re.search(rb"@([0-9]+)$", line)) and (moz_id := quote_voice(int(found[1]))) in mapping:
                groups.setdefault(f"quote slot {slot}", []).append(moz_id)
    select = list(range(PSP_SELECT_VOICES[0], PSP_SELECT_VOICES[0] + PSP_SELECT_VOICES[2]))
    assert {i for ids in groups.values() for i in ids} | set(select) == set(mapping)

    index = decoded_member(linkdata1[4058])
    with (game / "data/Sound/Voice/Voice.bin").open("rb") as bank:
        def stock_seconds(moz_id: int) -> float:
            offset, size = struct.unpack_from(">II", index, 8 + 8 * moz_id)
            bank.seek(offset)
            ogg = parse_kovs_record(bank.read(size), verify_checksums=False)[2]
            return ogg.sample_count / ogg.sample_rate
        stock = {i: stock_seconds(i) for i in mapping}
    with Iso9660(os.environ["WOZMOD_WO2_PSP_ISO"]) as disc, disc.open("PSP_GAME/USRDIR/sound/voice.bns") as japanese:
        header = japanese.read(16)
        table = japanese.read(8 * struct.unpack_from("<I", header, 4)[0])
        def psp_seconds(member: int) -> float:
            sector, _ = struct.unpack_from("<II", table, 8 * member)
            japanese.seek(sector * 2048)
            riff = japanese.read(128)  # RIFF, fmt (rate at 24), then fact (sample count)
            return struct.unpack_from("<I", riff, riff.index(b"fact") + 8)[0] / struct.unpack_from("<I", riff, 24)[0]
        psp = {i: psp_seconds(clip) for i, clip in mapping.items()}

    def agreement(ids: list[int]) -> float:
        padding = statistics.median(stock[i] - psp[i] for i in ids)
        return sum(abs(stock[i] - psp[i] - padding) <= 0.15 for i in ids) / len(ids)

    dream = statistics.median(stock[i] - psp[i] for o in range(132, 144) for i in groups[f"stage {o}"])
    low = {name: round(agreement(ids), 2) for name, ids in groups.items() if agreement(ids) < 0.4}
    low |= {f"select {i}": round(stock[i] - psp[i], 2) for i in select if abs(stock[i] - psp[i] - dream) > 0.15}
    assert {*(f"stage {o}" for o in range(132, 144)), "quote slot 0", "quote slot 1", "quote slot 70"} <= groups.keys()
    assert low == {}
