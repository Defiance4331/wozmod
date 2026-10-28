from dataclasses import replace
import struct

from wozmod.formats.scripts import Line, Table
from wozmod.text.script_alignment import (
    QUOTE_INSERT,
    WO1_QUOTE_INSERT,
    Script,
    StageJoin,
    caption_pairs,
    donor_event_lines,
    event_caption_pairs,
    event_pairs,
    moz_scripts,
    narration_pairs,
    notice_join,
    pair_scripts,
    person_map,
    quote_pairs,
    stage_join,
    x360_members,
)
from synthetic import event_file, mesc, script, spans, strings, write_ens


def _script(notices=(), dialog=()):
    return Script(
        Table(tuple(Line(b"32" + text, False) for text in notices)),
        Table(tuple(Line(b"00" + text + (suffix or b""), True) for suffix, text in dialog)),
    )


def _voices(indices):
    return _script(dialog=[(b"@%04d" % index, b"Fixture") for index in indices])


def _quotes(formats, officers, order="<"):
    return spans(
        [
            strings(*formats, order=order),
            strings(b"11Fixture %s", order=order),
            spans([strings(*lines, order=order) for lines in officers], order=order),
        ],
        struct.pack(f"{order}I", 3),
        order,
    )


def _event(*lines, order="<"):
    data = b"\0" * 12
    for index, text in lines:
        data += b"\xff" * 4 + struct.pack(f"{order}HH", index, 32) + text + b"\0"
        data += b"\0" * ((12 - len(data)) % 16)
    return data


def _telop(*cues):
    times = (time for start, end, _ in cues for time in (start, end))
    return struct.pack(f"<{1 + 2 * len(cues)}I", len(cues), *times) + b"".join(
        text + b"\0" for _, _, text in cues
    )


def test_script_pairing_takes_the_best_voice_union_ratio_at_or_above_the_threshold():
    # Bug: pairing by raw shared count (a donor with many extra voices wins), a threshold off its exact 0.9, a donor
    # under it accepted, or a voiceless stock row paired. Each case names the stock rows, the donors and the pairs.
    exact, subset = _voices(range(1, 11)), _voices(range(1, 10))
    cases = {
        "exact threshold, voiceless and unrelated rows": (
            {7: _voices(range(1, 10)), 8: _script(), 9: _voices([99])}, [exact], {7: exact}),
        "best ratio over shared count": ({3: _voices(range(1, 11))}, [_voices(range(1, 21)), subset], {3: subset}),
    }
    wrong = [name for name, (stock, donors, expected) in cases.items() if pair_scripts(stock, donors) != expected]
    assert wrong == []


def _notice(record, form=0):
    return (0x25, record, 0, 0, form, 0, 0, 0)


def _speak(person, line):
    return (0x26, person, line, 0, 0, 0, 0, 0)


def test_stage_join_pairs_lines_through_the_commands_that_speak_them():
    # Bugs: a line paired by its place in its table, so a line MOZ inserts shifts every later one onto the next donor
    # line; a line MOZ moves to the end of its table (Dream stage 51) loses its donor line; a donor speaker compared
    # without the person map (MOZ numbers persons 6 and 7 as 22 and 23 here) pairs nothing. Expected by hand from
    # the commands below.
    donor = replace(_script([b"n0", b"n1", b"n2"], [(None, b"d0"), (None, b"d1"), (None, b"d2")]), events=(
        (_notice(0), _speak(5, 0)), (_notice(1, form=1),), (_notice(2), _speak(6, 1)), (_speak(7, 2),)))
    stock = replace(_script([b"N0", b"N2"], [(None, b"D0"), (None, b"Inserted"), (None, b"Old D1"), (None, b"D2"),
                                             (None, b"D1")]), events=(
        (_notice(0), _speak(5, 0)), (_speak(9, 1),), (_notice(1), _speak(22, 4)), (_speak(23, 3),)))
    assert stage_join(stock, donor, {5: 5, 22: 6, 23: 7}) == {("dialog", 0): 0, ("dialog", 3): 2, ("dialog", 4): 1}


def test_person_map_learns_a_person_a_copied_event_hides_and_one_stock_names_by_two_ids():
    # Bugs: donor person 2's only opcode vote lands on stock 11, which donor person 1 holds, because the stock copies
    # an event (WO2 X360 person 542 in Dream stage 47), so 2 maps to nothing; donor person 4, whom the stock names 40
    # in one stage and 4 in another (PSP persons 92 and 93 in Z Dream stages 135, 142 and 141), maps only to the id
    # with more votes, so the other stage's lines pair with nothing. Expected by hand: stock speaker to donor speaker.
    def stage(*events):
        return replace(_script(), events=tuple(((0x26, person, 0, 0, 0, 0, 0, 0),) + (fill,) * fills
                                               for person, fills in events))

    fill = (0x10, 0, 0, 0, 0, 0, 0, 0)
    pairs = [(stage((11, 3), (13, 3)), stage((1, 3), (3, 3))),               # teaches 11 and 13
             (stage((11, 0), (11, 0), (12, 0), (13, 0)), stage((1, 0), (2, 0), (3, 0))),  # stock copies event 0
             (stage((40, 5)), stage((4, 5))), (stage((4, 0)), stage((4, 0)))]  # 4 is 40 in one stage, 4 in another
    assert person_map(pairs) == {11: 1, 12: 2, 13: 3, 40: 4, 4: 4}


def test_stage_join_pairs_the_lines_between_aligned_neighbours_where_each_side_holds_as_many():
    # Bugs: a line kept from pairing only by its speaker key loses its donor take: a donor person the person map
    # cannot learn (8 here), or a line no donor command speaks (donor line 3; Dream stage 60
    # line 47); or a line MOZ inserts (stock line 5) takes a donor line. Expected by hand from the commands below.
    def speaks(*people):
        return tuple(((0x26, person, line, 0, 0, 0, 0, 0),) for line, person in enumerate(people) if person is not None)

    donor = replace(_script([], [(None, b"d%d" % i) for i in range(6)]), events=speaks(5, 8, 5, None, 6, 6))
    stock = replace(_script([], [(None, b"D%d" % i) for i in range(7)]), events=speaks(5, 21, 5, 22, 6, 30, 6))
    assert stage_join(stock, donor, {5: 5, 6: 6}) == {("dialog", i): d for i, d in ((0, 0), (1, 1), (2, 2), (3, 3),
                                                                                    (4, 4), (6, 5))}


def test_a_line_whose_speak_the_streams_align_takes_the_donor_line_from_the_speaker_set_pair():
    # Bug: the table pairs donor line 1 with stock line 1 by speaker set alone (both person 32), and the stream pair
    # of stock line 2, whose speak sits where the donor speaks line 1 (same event, same commands around it), is
    # refused because that donor line is taken: line 2 keeps the Japanese voice and line 1 plays line 2's English
    # take (Dream stage 60 lines 27 and 54, decisions ledger text rows 22 and 25). Expected by hand.
    a, b, c, d = ((op, 0, 0, 0, 0, 0, 0, 0) for op in (5, 6, 7, 8))
    donor = replace(_script([], [(None, b"d0"), (None, b"d1")]), events=((_speak(1, 0), a), (b, _speak(32, 1), c)))
    stock = replace(_script([], [(None, b"D0"), (None, b"Other words"), (None, b"D1")]), events=(
        (_speak(1, 0), a), (b, _speak(32, 2), c), (d, _speak(32, 1))))
    assert stage_join(stock, donor, {1: 1, 32: 32}) == {("dialog", 0): 0, ("dialog", 2): 1}


def test_lines_whose_speaks_cross_follow_the_streams_not_the_table_order():
    # Bug: the table pairs one person's lines in their order (stock 1 and 2 with donor 1 and 2), where MOZ speaks
    # line 2 in the event in which the donor speaks line 1 and the other way round, so each plays and shows the
    # other's sentence (Dream stage 48 lines 33/34 and 36/37, decisions ledger text row 22). Expected by hand.
    a, b, c = ((op, 0, 0, 0, 0, 0, 0, 0) for op in (5, 6, 7))
    donor = replace(_script([], [(None, b"d0"), (None, b"d1"), (None, b"d2")]), events=(
        (a, _speak(1, 0)), (b, _speak(2, 1)), (c, _speak(2, 2))))
    stock = replace(_script([], [(None, b"D0"), (None, b"D2"), (None, b"D1")]), events=(
        (a, _speak(1, 0)), (b, _speak(2, 2)), (c, _speak(2, 1))))
    assert stage_join(stock, donor, {1: 1, 2: 2}) == {("dialog", 0): 0, ("dialog", 1): 2, ("dialog", 2): 1}


def test_a_new_line_spoken_where_the_donor_repeats_a_line_takes_that_line_too():
    # Bug: the table's gap fill gives a line MOZ added (stock 1) the donor line no donor command speaks (donor 1),
    # where the donor speaks line 0 a second time; or the line that takes donor line 0 there takes it from stock line
    # 0, whose own speak is donor line 0's too (Dream stage 60 lines 39 and 47). Expected by hand.
    a, b = ((op, 0, 0, 0, 0, 0, 0, 0) for op in (5, 6))
    donor = replace(_script([], [(None, b"d0"), (None, b"d1")]), events=((a, _speak(1, 0)), (b, _speak(1, 0))))
    stock = replace(_script([], [(None, b"D0"), (None, b"New")]), events=((a, _speak(1, 0)), (b, _speak(1, 1))))
    assert stage_join(stock, donor, {1: 1}) == {("dialog", 0): 0, ("dialog", 1): 0}


def test_a_greedy_stream_match_alone_does_not_take_a_paired_donor_line():
    # Bug: the stream pass takes a donor line from its table pair on the greedy match's word: the greedy match puts
    # stock line 2's speak on donor line 1 (the run "5, speak"), where the optimal alignments do not, and line 1
    # loses its take to a line the donor never speaks there (Dream stage 46 line 66 and twelve more lines).
    five, six = (5, 0, 0, 0, 0, 0, 0, 0), (6, 0, 0, 0, 0, 0, 0, 0)
    donor = replace(_script([], [(None, b"d0"), (None, b"d1")]), events=(
        (), (_speak(1, 0),), (six, five, _speak(2, 1))))
    stock = replace(_script([], [(None, b"D0"), (None, b"D1"), (None, b"D2")]), events=(
        (five, five, _speak(1, 0)), (five, five, _speak(2, 2), _speak(2, 1)), ()))
    assert stage_join(stock, donor, {1: 1, 2: 2}) == {("dialog", 0): 0, ("dialog", 1): 1}


def test_notice_join_pairs_each_notice_with_the_one_its_commands_counterpart_shows():
    # Bugs: a notice paired by its place in its table, so the donor notice MOZ drops (here donor 2) puts the next
    # stock notice on it; a greedy stream match that takes the run of three commands and loses the notice MOZ moved
    # in front of them (stage 44); a notice paired across other words 3 and 4 (stock 2 has format 1, as donor 3 does,
    # and donor 2 has format 0); a notice MOZ shows from two copies of one event (stage 54) paired with one copy's
    # record. Expected by hand from the commands below.
    a, b, c = ((op, 0, 0, 0, 0, 0, 0, 0) for op in (0x0A, 0x23, 0x05))
    donor = replace(_script([b"n0", b"n1", b"n2", b"n3", b"n4"]), events=(
        (_notice(0), a), (b, b, b, _notice(1), c), (_notice(2), a), (_notice(3, form=1), a), (_notice(4), c)))
    stock = replace(_script([b"N0", b"N1", b"N2", b"N3", b"N4"]), events=(
        (_notice(0), a), (_notice(1), b, b, b, c), (_notice(2, form=1), a), (_notice(3), c), (_notice(4), c)))
    assert notice_join(StageJoin("wo2-x360", 0, donor, {}, {}, stock)) == {0: 0, 1: 1, 2: 3}


def _container(*entries):
    """A little-endian LINKDATA1 container: magic, count, 8 bytes, then (offset / 16, size) per entry."""
    head = 16 + 8 * len(entries)
    at, pairs, body = head + -head % 16, [], b""
    for entry in entries:
        pairs += [at // 16, len(entry)]
        body += entry + b"\0" * (-len(entry) % 16)
        at += len(entry) + -len(entry) % 16
    head_bytes = b"\x59\x3b\x03\x00" + struct.pack(f"<3I{len(pairs)}I", len(entries), 0, 0, *pairs)
    return head_bytes + b"\0" * (-len(head_bytes) % 16) + body


def test_camp_talk_lines_pair_by_scene_and_line_not_by_their_place_in_the_mesc():
    # Bug: camp talk lines paired by their place in the one MESC (or only where the MESCs have equal lengths), so a
    # scene MOZ gives one more line shifts every later scene's lines onto the next donor line. Oracle: line j of scene
    # s is MESC line j plus the line counts of the scenes before (both draws), here with a line MOZ added to scene 0.
    stock = _container(b"\x03", b"\x01", mesc(b"S0", b"S1", b"Added", b"T0"))
    donor = _container(b"\x02", b"\x01", mesc(b"Fixture S0", b"Fixture S1", b"Fixture T0"))
    assert narration_pairs(4105, stock, donor) == [(b"S0", "Fixture S0"), (b"S1", "Fixture S1"), (b"T0", "Fixture T0")]


def test_event_pairing_joins_line_ids_across_every_donor_event_file_with_both_word_orders():
    # A line index names one voiced line game-wide. Bugs: joining only the one donor file paired with the stock event
    # drops a line another file shows (MOZ event 283 merges WO1 officer variants); joining by order shifts lines;
    # an index two donor files caption differently is paired at all; the 0x08 byte MOZ leaves at a line is paired.
    stock = _event((5, b"\x82\xa01"), (6, b"\x82\xa02"), (7, b"\x82\xa03"), (9, b"\x82\xa04"), (4, b"\x82\xa05"), (8, b"\x08"))
    for order in "<>":
        donor = [_event((7, b"Fixture C"), (5, b"Fixture A"), (4, b"Fixture D"), order=order),
                 _event((9, b"Fixture B"), (5, b"Fixture A"), (8, b"Fixture extra"), (4, b"Fixture E"), order=order)]
        assert event_pairs(stock, donor_event_lines(donor, order)) == [
            (b"\x82\xa01", "Fixture A"), (b"\x82\xa03", "Fixture C"), (b"\x82\xa04", "Fixture B")]


def test_event_caption_pairing_joins_place_captions_by_sub_scene_and_order_inside_it():
    # Place captions carry no line index. Bugs: pairing by order over the whole file shifts every caption after a
    # sub-scene where the donor has one more; a caption the donor left in Japanese is paired.
    stock, _ = event_file([(5, 0, b"S1")], [(5, 0, b"S2"), (5, 0, b"S3")], [(5, 0, b"S4")])
    donor, _ = event_file([(5, 0, b"Fixture A"), (5, 0, b"Fixture extra")],
                          [(5, 0, b"Fixture B"), (3, 0, b"Fixture other"), (5, 0, b"Fixture C")],
                          [(5, 0, b"\x82\xa0\x82\xa2")], order=">", gap=4)
    assert event_caption_pairs(stock, donor, ">") == [(b"S1", "Fixture A"), (b"S2", "Fixture B"), (b"S3", "Fixture C")]


def test_caption_pairing_merges_split_cues_using_greatest_overlap_and_later_ties():
    stock = _telop((0, 10, b"S1"), (10, 20, b"S2"), (25, 40, b"S3"))
    donor = _telop(
        (0, 4, b"Fixture \n A"), (4, 8, b"B"), (5, 15, b"C"),
        (19, 25, b"D"), (25, 30, b" "), (30, 35, b"E"), (50, 60, b"Outside"),
    )
    assert caption_pairs(stock, donor) == [
        (b"S1", b"Fixture A B"), (b"S2", b"C D"), (b"S3", b"E"),
    ]


def test_quote_pairing_joins_officer_tables_by_officer_id_for_each_donor():
    # Catches a donor's officers shifted by the stock officers it lacks: X360 lacks MOZ 94-95; WO1 has 0-78, then
    # its generic officers 79-87, which are MOZ 96-104.
    stock = _quotes([b"33F1", b"11F2"], [[b"00S%d@%d" % (index, 5000 + index), b""] for index in range(107)])
    donors = {QUOTE_INSERT: (105, lambda i: None if i in (94, 95) else i if i < 94 else i - 2),
              WO1_QUOTE_INSERT: (88, lambda i: i if i < 79 else i - 17 if 96 <= i <= 104 else None)}
    for insert, (count, theirs) in donors.items():
        expected = {b"F1": "Fixture one", b"F2": "Fixture two", b"Fixture %s": "Fixture %s"}
        expected |= {b"S%d" % i: f"Fixture {theirs(i)}" for i in range(107) if theirs(i) is not None}
        for order in "<>":
            donor = _quotes([b"33Fixture one", b"11Fixture two"],
                            [[b"00Fixture %d@%d" % (index, 4000 + index), b"00Extra@1"] for index in range(count)], order)
            assert dict(quote_pairs(stock, donor, order=order, insert=insert)) == expected, (insert, order)


def test_quote_pairing_with_officers_disabled_keeps_only_the_two_format_tables():
    stock = _quotes([b"33S1"], [[b"00Officer stock@1"]])
    donor = _quotes([b"33Fixture format"], [[b"00Fixture officer@2"]])
    assert quote_pairs(stock, donor, officers=False) == [
        (b"S1", "Fixture format"), (b"Fixture %s", "Fixture %s"),
    ]


def test_x360_member_reader_does_not_shift_ps2_member_ids_or_relabel_results(tmp_path):
    values = {17: b"Fixture quote", 103: b"Fixture first script", 275: b"Fixture last event"}
    ens = write_ens(tmp_path, values, 276)
    assert x360_members(ens, [2968, 45, 2796]) == {
        2968: b"Fixture last event", 45: b"Fixture quote", 2796: b"Fixture first script",
    }


def test_stock_script_reader_excludes_multimember_entries_and_invalid_script_headers(tmp_path):
    data = script(strings(b"32Fixture notice"), strings(b"00Fixture dialog@0001"))
    allocation = lambda data: data + bytes(-len(data) % 2048)
    body = allocation(data) + allocation(data[:24]) + allocation(data[24:]) + allocation(struct.pack("<I", 4))
    index = struct.pack("<9I", len(data), 0, 0, 1, 4096, 0, 4, 0, 0)
    index += struct.pack("<3I", 24, len(data) - 24, 0)
    (tmp_path / "LINKDATA2.BIN").write_bytes(body)
    (tmp_path / "LINKDATA2.IDX").write_bytes(index)
    found = moz_scripts(tmp_path / "LINKDATA2")
    assert {ordinal: (entry.notices.records[0].text_bytes, entry.dialog.records[0].text_bytes) for ordinal, entry in found.items()} == {
        0: (b"Fixture notice", b"Fixture dialog"),
    }
