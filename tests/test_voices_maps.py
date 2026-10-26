import json
import os
from dataclasses import replace
from pathlib import Path

import pytest


from wozmod.formats.scripts import Line, Script, Table
from wozmod.text.script_alignment import stage_joins
from wozmod.voices import (
    VoiceSlot, dialogue_voice_maps, lines_apart, psp_voice_map, slot_records, stage_triggers, voice_slots,
)
from wozmod.voices.maps import story_voice_maps


def _speaks(lines: tuple[Line, ...], flags: list[int]) -> Script:
    """A stage script whose event i speaks line i, by person i, with bank flag flags[i]."""
    events = tuple(((0x26, i, i, 0, 0, 0, 0, flag),) for i, flag in enumerate(flags))
    return Script(Table(()), Table(lines), {i: {flag} for i, flag in enumerate(flags)}, events)


def _script(*suffixes):
    return _speaks(tuple(Line(b"00Fixture@%04d" % n, True) for n in suffixes), [0] * len(suffixes))


def _dreams():
    return ({o: _script() for o in range(40, 68)}, {m: _script() for m in range(2836, 2864)})


def _story(stock: dict[int, Script], wo1: dict[int, Script] | None = None, wo2: dict[int, Script] | None = None):
    """The story voice maps (WO1 X360, WO2 X360) and the stage triggers, as dialogue_voice_maps builds them."""
    joins = stage_joins(stock, wo1 or {}, wo2 or {})
    triggers = stage_triggers(stock, joins)
    maps = story_voice_maps(stock, joins, triggers)
    return maps["wo1-x360"], maps["wo2-x360"], triggers


def _spoken(*lines: tuple[bytes, int]) -> Script:
    """A stage script whose event i speaks line i, given as (value, person)."""
    events = tuple(((0x26, person, i, 0, 0, 0, 0, 0),) for i, (_, person) in enumerate(lines))
    return Script(Table(()), Table(tuple(Line(value, True) for value, _ in lines)), {i: {0} for i in range(len(lines))},
                  events)


def test_story_maps_do_not_cross_mode_boundaries_or_shift_suffix_one():
    stock = {o: _script(1, 9) for o in (0, 39, 40, 71, 72, 131, 132)}
    assert _story(stock, wo1={900: _script(1, 9)})[0] == {3144: 1473, 3152: 1481}
    assert _story(stock, wo2={2796: _script(1, 9)})[1] == {12408: 1644, 12416: 1652}
    for ordinal in (72, 131):
        assert _story({ordinal: stock[ordinal]}, wo1={900: _script(1, 9)})[0] == {3144: 1473, 3152: 1481}
    for ordinal in (0, 39):
        assert _story({ordinal: stock[ordinal]}, wo2={2796: _script(1, 9)})[1] == {12408: 1644, 12416: 1652}
    for ordinal in (40, 71, 132):
        assert _story({ordinal: stock[ordinal]}, wo1={900: _script(1, 9)})[0] == {}
    for ordinal in (40, 72, 131):
        assert _story({ordinal: stock[ordinal]}, wo2={2796: _script(1, 9)})[1] == {}


def test_story_maps_do_not_claim_a_suffix_missing_from_the_paired_donor():
    stock = _script(*range(1, 11))
    donor = _script(*range(1, 10))
    assert _story({72: stock}, wo1={579: donor})[0] == {3144 + i: 1473 + i for i in range(9)}
    assert _story({0: stock}, wo2={2796: donor})[1] == {12408 + i: 1644 + i for i in range(9)}


def test_story_voice_follows_the_command_pair_not_the_shared_suffix():
    # Bugs: the voice joins by shared suffix, so where the donor files the two sentences of one beat under each
    # other's suffix the line plays its neighbour's sentence (WO1 stage 81 line 10, and stage 107 lines 31 and 32 when
    # a subtitle row moved the clip); a line whose counterpart reuses another suffix gets no clip at all (WO1 stage 82
    # line 11, decisions ledger text row 22). Lines 0 to 18 share their suffixes 1 to 19. Line 19 (@0500, person 19)
    # and line 20 (@0501, person 20) are spoken where the donor speaks @0501 and @0500; line 21 (@0585) where the donor
    # speaks @2631. Expected by hand: id is suffix less one on 0xC48, record suffix less one on 1473.
    shared = [(b"00Fixture@%04d" % n, n) for n in range(1, 20)]
    stock = _spoken(*shared, (b"00S19@0500", 19), (b"00S20@0501", 20), (b"00S21@0585", 21))
    donor = _spoken(*shared, (b"00d19@0501", 19), (b"00d20@0500", 20), (b"00d21@2631", 21))
    wo1, _, _ = _story({72: stock}, wo1={579: donor})
    assert {i: wo1.get(3144 + i - 1) for i in (500, 501, 585, 2631)} == {500: 1473 + 500, 501: 1473 + 499,
                                                                            585: 1473 + 2630, 2631: None}


def test_lines_that_share_a_voice_id_and_disagree_keep_its_record_and_are_listed():
    # Bugs: one id spoken by two lines whose counterparts are different donor lines plays whichever line came last
    # (WO2 story stage 1 lines 11 and 12 share @0066; the donor files line 12 under @0067); a story line takes the id
    # from the Dream line it borrowed the number from (story @3649 in stage 38, the Dream line's own counterpart is
    # another suffix); the line that cannot be voiced is not listed for a slot of its own (decisions ledger text row
    # 23). Stage 0 lines 0 to 39 share suffixes 1 to 40 with the donor; lines 40 and 41 (persons 41, 42) speak @0066,
    # the donor's lines @0066 and @0067; line 42 (person 43) speaks @3649, the donor's @2762. Dream stage 40 line 0
    # (person 50) speaks @3649 too; its donor line is @3491. Expected by hand from the bases.
    stock, donor = _dreams()
    shared = [(b"00Fixture@%04d" % n, n) for n in range(1, 41)]
    stock[0] = _spoken(*shared, (b"00S40@0066", 41), (b"00S41@0066", 42), (b"00S42@3649", 43))
    donor[2796] = _spoken(*shared, (b"00d40@0066", 41), (b"00d41@0067", 42), (b"00d42@2762", 43))
    stock[40], donor[2836] = _spoken((b"00Y@3649", 50)), _spoken((b"00y@3491", 50))
    _, wo2, triggers = _story(stock, wo2=donor)
    _, played = dialogue_voice_maps(stock, {}, donor, [], [])
    assert (wo2[12408 + 65], 12408 + 3648 in wo2, played[12408 + 3648]) == (1644 + 65, False, 1644 + 3490)
    assert lines_apart(triggers, {}, played) == {(0, 41): ("wo2-x360", 1644 + 66), (0, 42): ("wo2-x360", 1644 + 2761)}


def test_each_line_a_shared_id_cannot_voice_gets_a_slot_that_plays_its_trigger_take():
    # Bugs: a slot numbered inside the stock ids, so its take replaces another line's; two lines given one slot; a
    # slot that plays the shared id's record (the partner's take) instead of the line's own trigger record; a slot
    # that lists another line's stock id, so the runtime swaps the wrong speak (decisions ledger text row 23).
    # Stage 0 lines 40 and 41 speak @0066, line 42 speaks @3649 like Dream stage 40 line 0 (the shapes of the test
    # above). Expected by hand: slots from 22665 in (stage, line) order; ids are suffix less one on 0x3078, records
    # suffix less one on 1644.
    stock, donor = _dreams()
    shared = [(b"00Fixture@%04d" % n, n) for n in range(1, 41)]
    stock[0] = _spoken(*shared, (b"00S40@0066", 41), (b"00S41@0066", 42), (b"00S42@3649", 43))
    donor[2796] = _spoken(*shared, (b"00d40@0066", 41), (b"00d41@0067", 42), (b"00d42@2762", 43))
    stock[40], donor[2836] = _spoken((b"00Y@3649", 50)), _spoken((b"00y@3491", 50))
    _, _, triggers = _story(stock, wo2=donor)
    _, played = dialogue_voice_maps(stock, {}, donor, [], [])
    slots = voice_slots(triggers, {}, played)
    assert slots == {(0, 41): VoiceSlot(22665, (12408 + 65,), "wo2-x360", 1644 + 66),
                     (0, 42): VoiceSlot(22666, (12408 + 3648,), "wo2-x360", 1644 + 2761)}
    assert slot_records(slots, "wo2-x360") == {22665: 1644 + 66, 22666: 1644 + 2761}


def test_wo1_quote_slot_keeps_its_record_when_the_english_differs():
    # Bug: a WO1 X360 quote slot is voiced only where its words are the Xbox 360 line's, so a slot whose WO2 clip is
    # missing or a placeholder loses the WO1 take that would play there. Ids by hand: suffix less one on 0x3078,
    # WO1 record suffix less one on 1473.
    stock, donor = _dreams()
    ours = [b"00S1@8000", b"00S2@8001", b"00S3@8002"]
    one = [b"00Fixture A@0007", b"00Fixture B@0008", b"00Fixture C@0009"]
    two = [b"12Fixture A@0011", b"00Fixture other@0012", b""]
    wo1, _ = dialogue_voice_maps(stock, {}, donor, [(ours, one)], [(ours, two)])
    assert {k: v for k, v in wo1.items() if k in range(20407, 20410)} == {20407: 1479, 20408: 1480, 20409: 1481}


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


# The PSP's fixed block for the Z Dream briefings: MOZ stages 0x84-0x8F play 0x1C0 + stage, the PSP its stages
# 0x48-0x53 on 0x102 + stage (tests/test_voice_blocks.py joins them against both programs).
Z_DREAM_BRIEFINGS = {580 + i: 330 + i for i in range(12)}


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
    expected |= {12408 + 6999: 1419 + 200, 94: 94, 95: 95} | Z_DREAM_BRIEFINGS
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
    assert psp_voice_map({143: flagged, 66: plain}, psp, []) == {flagged_id: 1419 + 6, 0x3078 + 4: 1419 + 5, 94: 94, 95: 95,
                                                                 **Z_DREAM_BRIEFINGS}
    stock, donor = _dreams()
    stock[40], stock[66], donor[2836], donor[2862] = flagged, plain, _script(7, 6), Script(Table(()), Table((Line(b"00T", True),)))
    _, wo2 = dialogue_voice_maps(stock, {}, donor, [], [])
    assert (wo2.get(flagged_id), wo2.get(0x3078 + 4), plain_id in wo2) == (1644 + 6, 1644 + 5, False)


@pytest.fixture(scope="module")
def stock_maps():
    """The installer's stage triggers, its dialogue maps (WO1 X360, WO2 X360, WO2 PSP), the stock scripts and the
    stage joins, built from the stock game and donors as voices.build builds them."""
    from wozmod.donors import DonorsFolder
    from wozmod.formats.linkdata import archive
    from wozmod.voices.build import PSP_TEXT, psp_text_voice_map
    from wozmod.text.script_alignment import (
        QUOTES, WO1_QUOTE_INSERT, moz_scripts, paired_quote_tables, wo1_scripts, wo2_x360,
    )

    game, folders = Path(os.environ["WOZMOD_GAME"]), DonorsFolder(Path(os.environ["WOZMOD_DONORS"]))
    wo1, wo2 = folders.folder("wo1-x360"), folders.folder("wo2-x360")
    moz, wo2_donor = moz_scripts(game / "LINKDATA2"), wo2_x360(wo2 / "linkdata_ens")
    wo1_by_ens = wo1_scripts(wo1 / "linkdata_ens")
    quotes_entry, wo1_quotes, wo2_quotes = QUOTES
    quotes = archive(game / "LINKDATA1")[quotes_entry]
    joins = stage_joins(moz, wo1_by_ens, wo2_donor.scripts())
    one, two = dialogue_voice_maps(
        moz, wo1_by_ens, wo2_donor.scripts(),
        paired_quote_tables(quotes, archive(wo1 / "linkdata_ens")[wo1_quotes], ">", insert=WO1_QUOTE_INSERT),
        paired_quote_tables(quotes, wo2_donor.members([wo2_quotes])[wo2_quotes], wo2_donor.order), joins)
    three = psp_text_voice_map(moz, quotes, folders.folder("wo2-psp") / PSP_TEXT)
    return stage_triggers(moz, joins), one, two, three, moz, joins


INPUTS = pytest.mark.skipif(not (os.environ.get("WOZMOD_GAME") and os.environ.get("WOZMOD_DONORS")),
                            reason="set WOZMOD_GAME and WOZMOD_DONORS")


@INPUTS
@pytest.mark.slow_first
def test_stock_lines_play_the_take_their_trigger_plays(stock_maps):
    # Bugs: WO1 stage 82 line 11 (voice 3728) left Japanese because its counterpart reuses another suffix; stage 107
    # lines 31 and 32 (5562, 5563) swapped by their subtitle rows; stage 81 line 10 (3663) on its neighbour's shared
    # suffix; Dream stage 60 line 54 (16439) refused WO2 X360 line 27's take, which line 27 (16412) kept on its
    # speaker set alone; Dream stage 48's crossed lines each play the other's take; Dream 60.47 plays a donor line
    # no donor command speaks. Oracle: the trigger audit's records (work/handbacks/trigger-audit.md, trigger-stage.md:
    # the donor line both optimal alignments give each speak, record = suffix less one plus the disc base) and
    # decisions ledger text rows 22 and 25.
    _, one, two, _, _, _ = stock_maps
    assert {i: one.get(i) for i in (3728, 5562, 5563, 3663)} == {3728: 4103, 5562: 3891, 5563: 3892, 3663: 1991}
    assert (two.get(16439), 16412 in two, 16412 in one) == (5430, False, False)
    # Dream stage 48 lines 33, 34, 36 and 37 (the streams cross the table order) and 60.47 (spoken where the donor
    # repeats line 39): record = donor suffix less one plus 1644.
    assert {i: two.get(i) for i in (15669, 15670, 15672, 15673, 16432)} == {
        15669: 4822, 15670: 4823, 15672: 4819, 15673: 4820, 16432: 5442}


@INPUTS
def test_lines_that_share_a_voice_id_with_another_trigger_stay_listed(stock_maps):
    # Bugs: a change moves a shared id's clip onto the line that borrowed it, so its owner hears the other sentence;
    # a new shared id appears whose lines want different takes, and one of them silently hears the other's.
    # Oracle: decisions ledger text row 23, the eight lines the trigger audit found.
    triggers, one, two, _, _, _ = stock_maps
    assert set(lines_apart(triggers, one, two)) == {(1, 12), (113, 8), (28, 14), (38, 82), (96, 84), (96, 85),
                                                    (101, 63), (110, 66)}


@INPUTS
def test_each_line_of_a_shared_voice_id_plays_its_own_trigger_take_and_shows_its_line(stock_maps):
    # Bugs: a slotted line plays its partner's take (the slot keeps the shared id's record) or another line's slot;
    # the partner loses its take to the slot; a line's own row points at the partner's donor line, so it reads one
    # sentence and hears another; the partner's shared row moved to the slotted line's donor line. Oracle: the
    # trigger records of decisions ledger text row 23 (work/handbacks/trigger-maps-impl.md), and for every line the
    # @NNNN suffix of the donor line its subtitle row points at, which names the take the donor plays with that line.
    import hashlib

    from wozmod.text.contexts import lookup_keys
    from wozmod.formats.audio import WO1_RECORD_BASE, WO2_CLIP_BASE

    _, one, two, _, moz, joins = stock_maps
    triggers = stage_triggers(moz, joins)
    slots = voice_slots(triggers, one, two)
    assert {line: (s.source, s.record) for line, s in slots.items()} == {
        (1, 12): ("wo2-x360", 1710), (28, 14): ("wo2-x360", 3578), (38, 82): ("wo2-x360", 4405),
        (96, 84): ("wo1-x360", 7361), (96, 85): ("wo1-x360", 7397), (101, 63): ("wo1-x360", 8375),
        (110, 66): ("wo1-x360", 8830), (113, 8): ("wo1-x360", 1526)}
    rows = {r["key"]: r for r in json.loads((Path(__file__).resolve().parents[1] / "data/text/table.json").read_text(
        encoding="utf-8"))["rows"]}
    base = {"wo1-x360": WO1_RECORD_BASE, "wo2-x360": WO2_CLIP_BASE}

    def shown(o: int, i: int) -> tuple[str, int] | None:
        """The take the donor plays with the donor line the line's subtitle row points at."""
        for key in lookup_keys("stage", moz[o].dialog.records[i].value[2:], line=(o, i)):
            if row := rows.get(hashlib.sha256(key).hexdigest()):
                source, _, entry, _, d = row["ref"]
                donor = next(j.donor for j in joins.values() if (j.source, j.entry) == (source, entry))
                return source, int(donor.dialog.records[d].dialog_suffix[1:]) - 1 + base[source]
        return None

    partners = {(p, k) for o, i in slots for p, script in moz.items() for k, r in enumerate(script.dialog.records)
                if r.value == moz[o].dialog.records[i].value and (p, k) != (o, i)}
    played = {line: (slots[line].source, slots[line].record) for line in slots}
    played |= {(p, k): ("wo2-x360", two[v]) if v in two else ("wo1-x360", one[v])
               for p, k in partners for v in triggers[p, k][0]}
    assert len(partners) == 8
    assert {line: shown(*line) for line in played} == played


@INPUTS
def test_only_the_reviewed_lines_keep_their_japanese_voice(stock_maps):
    # Bug: a pairing change (stage_join, the person map, a voice map rule) silently drops a spoken line to its
    # Japanese voice, or gives one of the lines no donor speaks at its trigger another line's English take.
    # Oracle: data/text/japanese_voices.json, the lines the trigger sweep (work/handbacks/trigger-stage.md) found
    # with no counterpart in any donor, reviewed against decisions ledger text rows 21, 22 and 25.
    from wozmod.formats.audio import stage_line_voices

    _, one, two, three, moz, _ = stock_maps
    listed = {(row["line"], row["voice"]) for row in json.loads(
        (Path(__file__).resolve().parents[1] / "data/text/japanese_voices.json").read_text())["lines"]}
    japanese = {(f"{o}.{i}", voice) for o, script in moz.items() for i, ids in stage_line_voices(o, script).items()
                for voice in ids if voice not in one and voice not in two and voice not in three}
    differing = sorted(f"{line} voice {voice}: " + ("lost its English clip" if (line, voice) in japanese
                                                    else "gained an English clip")
                       for line, voice in japanese ^ listed)
    assert not differing, f"{len(differing)} lines: " + "; ".join(differing)
