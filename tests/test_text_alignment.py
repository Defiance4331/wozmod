import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from wozmod.formats.scripts import Line, Table
from wozmod.text.script_alignment import (
    NARRATION,
    QUOTE_INSERT,
    WO1_QUOTE_INSERT,
    Script,
    caption_pairs,
    event_pairs,
    moz_scripts,
    narration_pairs,
    official_pairs,
    pair_scripts,
    prefix_pairs,
    quote_pairs,
    record_pairs,
    wo2_x360,
    x360_members,
)


def _script(notices=(), dialog=()):
    return Script(
        Table(tuple(Line(b"32" + text, False) for text in notices)),
        Table(tuple(Line(b"00" + text + (suffix or b""), True) for suffix, text in dialog)),
    )


def _voices(indices):
    return _script(dialog=[(b"@%04d" % index, b"Fixture") for index in indices])


def _strings(*lines, order="<"):
    offsets, body = [], b""
    for line in lines:
        offsets.append(4 + 4 * len(lines) + len(body))
        body += line + b"\0"
    return struct.pack(f"{order}{1 + len(lines)}I", len(lines), *offsets) + body


def _spans(parts, head=b"", order="<"):
    offset, table = len(head) + 8 * len(parts), b""
    for part in parts:
        table += struct.pack(f"{order}II", offset, len(part))
        offset += len(part)
    return head + table + b"".join(parts)


def _quotes(formats, officers, order="<"):
    return _spans(
        [
            _strings(*formats, order=order),
            _strings(b"11Fixture %s", order=order),
            _spans([_strings(*lines, order=order) for lines in officers], order=order),
        ],
        struct.pack(f"{order}I", 3),
        order,
    )


def _mesc(*lines):
    offsets, body = [], b""
    for line in lines:
        offsets.append(12 + 4 * len(lines) + len(body))
        body += line + b"\0"
    size = 12 + 4 * len(lines) + len(body)
    return b"MESC" + struct.pack(f"<II{len(lines)}I", len(lines), size, *offsets) + body


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


def _script_bytes():
    notices = _strings(b"32Fixture notice")
    notices += bytes(-len(notices) % 4)
    dialog = _strings(b"00Fixture dialog@0001")
    return struct.pack("<5I", 4, 20, 24, 28, 28 + len(notices)) + b"HEADTAIL" + notices + dialog


def test_script_pairing_keeps_the_exact_union_threshold_and_skips_voiceless_rows():
    stock = {7: _voices(range(1, 10)), 8: _script(), 9: _voices([99])}
    accepted = _voices(range(1, 11))
    assert pair_scripts(stock, [accepted]) == {7: accepted}


def test_script_pairing_chooses_the_best_union_ratio_instead_of_raw_shared_count():
    stock = _voices(range(1, 11))
    too_many_extra = _voices(range(1, 21))
    exact_subset = _voices(range(1, 10))
    assert pair_scripts({3: stock}, [too_many_extra, exact_subset]) == {3: exact_subset}


def test_record_pairing_drops_duplicate_voice_candidates_and_incomplete_notice_prefixes():
    stock = _script(
        [b"Stock notice"],
        [(b"@0001", b"S1"), (b"@0002", b"S2"), (b"@0003", b"S3"), (None, b"S4")],
    )
    donor = _script(
        [b"Fixture first", b"Fixture second"],
        [(b"@0001", b"Fixture A"), (b"@0002", b"Fixture B"), (b"@0002", b"Fixture C"),
         (b"@0003", b"\x82\xa0"), (None, b"Fixture unsuffixed")],
    )
    assert record_pairs(stock, donor) == [(b"S1", "Fixture A")]


def test_prefix_pairing_keeps_renumbered_dialog_and_stops_before_appended_rows():
    stock = _script([b"N1", b"N2", b"Append"], [(b"@0102", b"S1"), (b"@0103", b"S2"), (b"@0104", b"Append")])
    donor = _script([b"Fixture first", b"Fixture second"], [(b"@0100", b"Fixture A"), (b"@0101", b"Fixture B")])
    assert prefix_pairs(stock, donor) == [
        (b"N1", "Fixture first"), (b"N2", "Fixture second"),
        (b"S1", "Fixture A"), (b"S2", "Fixture B"),
    ]


def test_narration_pairing_drops_unequal_blocks_and_blank_stock_slots():
    stock = _mesc(b"S1", b"\x81\x40", b"S3") + _mesc(b"S4", b"S5")
    donor = _mesc(b"Fixture A", b"Fixture blank", b"Fixture C") + _mesc(b"Fixture mismatch")
    assert narration_pairs(stock, donor) == [(b"S1", "Fixture A"), (b"S3", "Fixture C")]


@pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                    reason="set WOZMOD_DONORS and WOZMOD_GAME")
def test_each_narration_donor_entry_has_the_stock_pages_block_for_block():
    # Catches a narration entry off by one (WO1 X360 still pages are ENS 6, which 0x8226F09C loads; ENS 7 has no
    # pages), which pairs no page at all.
    from wozmod.formats.linkdata import moz_entries, wo1_entries
    from wozmod.formats.scripts import mesc_blocks

    donors, game = Path(os.environ["WOZMOD_DONORS"]), Path(os.environ["WOZMOD_GAME"])
    stock = moz_entries(game / "LINKDATA1.BIN", game / "LINKDATA1.IDX", [n for n, _, _ in NARRATION])
    wo1 = wo1_entries(donors / "wo1-x360/linkdata_ens.lnk", donors / "wo1-x360/linkdata_ens.idx",
                      [e for _, s, e in NARRATION if s == "wo1"])
    wo2 = wo2_x360(donors / "wo2-x360/linkdata_ens").members([e for _, s, e in NARRATION if s == "wo2"])
    unequal = [n for n, s, e in NARRATION if [len(b) for b in mesc_blocks(stock[n])]
               != [len(b) for b in mesc_blocks((wo1 if s == "wo1" else wo2)[e])]]
    assert unequal == []


def test_event_pairing_joins_ids_instead_of_order_with_both_donor_word_orders():
    stock = _event((5, b"S1"), (6, b"S2"), (7, b"S3"))
    for order in "<>":
        donor = _event((7, b"Fixture C"), (5, b"Fixture A"), (8, b"Fixture extra"), order=order)
        assert event_pairs(stock, donor, order) == [(b"S1", "Fixture A"), (b"S3", "Fixture C")]


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
    records, body, sector = [], b"", 0
    for ordinal in range(276):
        value = values.get(ordinal, b"")
        allocation = int(bool(value))
        records.append(struct.pack(">4I", sector, allocation, len(value), 0))
        if value:
            body += value + bytes(2048 - len(value))
        sector += allocation
    ens = tmp_path / "linkdata_ens"
    ens.with_suffix(".idx").write_bytes(struct.pack(">4sIII", b"SM4L", 276, sector, 0) + b"".join(records))
    ens.with_suffix(".lnk").write_bytes(body)
    assert x360_members(ens, [2968, 45, 2796]) == {
        2968: b"Fixture last event", 45: b"Fixture quote", 2796: b"Fixture first script",
    }


def test_stock_script_reader_excludes_multimember_entries_and_invalid_script_headers(tmp_path):
    script = _script_bytes()
    allocation = lambda data: data + bytes(-len(data) % 2048)
    body = allocation(script) + allocation(script[:24]) + allocation(script[24:]) + allocation(struct.pack("<I", 4))
    index = struct.pack("<9I", len(script), 0, 0, 1, 4096, 0, 4, 0, 0)
    index += struct.pack("<3I", 24, len(script) - 24, 0)
    bin_path, idx_path = tmp_path / "LINKDATA2.BIN", tmp_path / "LINKDATA2.IDX"
    bin_path.write_bytes(body)
    idx_path.write_bytes(index)
    found = moz_scripts(bin_path, idx_path)
    assert {ordinal: (entry.notices.records[0].text_bytes, entry.dialog.records[0].text_bytes) for ordinal, entry in found.items()} == {
        0: (b"Fixture notice", b"Fixture dialog"),
    }


def _baseline_module(old_repo, tmp_path):
    prefix = "src/musou_patch/targets/musou_orochi_z/"
    for name, source_name in (("_text_oracle_archives", "archives"), ("_text_oracle_alignment", "script_alignment")):
        source = subprocess.run(
            ["git", "-C", str(old_repo), "show", f"2fa29d5:{prefix}{source_name}.py"],
            capture_output=True, text=True, check=True,
        ).stdout
        source = source.replace("from .archives import", "from _text_oracle_archives import")
        path = tmp_path / f"{name}.py"
        path.write_text(source)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _fingerprint(rows):
    payload = [(key.hex(), english, source) for key, (english, source) in sorted(rows.items())]
    return len(payload), hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.skipif(os.environ.get("WOZMOD_TEXT_ALIGNMENT") != "1", reason="set WOZMOD_TEXT_ALIGNMENT=1 for donor alignment oracle")
def test_official_pairing_rederivation_keeps_every_baseline_vote_source_and_voice_suffix(tmp_path):
    old_value, game_value = os.environ.get("WOZMOD_OLD_REPO"), os.environ.get("WOZMOD_GAME")
    if not old_value or not game_value:
        pytest.skip("set WOZMOD_OLD_REPO and WOZMOD_GAME for real inputs")
    old_repo, game = Path(old_value), Path(game_value)
    wo1 = old_repo / "work/donors/wo1/linkdata_ens"
    x360 = old_repo / "work/donors/wo2-x360/linkdata_ens"
    if not all(path.is_file() for path in [
        game / "LINKDATA1.BIN", game / "LINKDATA1.IDX", game / "LINKDATA2.BIN", game / "LINKDATA2.IDX",
        wo1.with_suffix(".lnk"), wo1.with_suffix(".idx"), x360.with_suffix(".lnk"), x360.with_suffix(".idx"),
    ]):
        pytest.skip("stock and extracted donor inputs are absent")
    baseline = _baseline_module(old_repo, tmp_path)
    expected = baseline.official_pairs(game, wo1, baseline.wo2_x360(x360))
    actual = official_pairs(game, wo1, wo2_x360(x360))
    assert _fingerprint(actual) == _fingerprint(expected)
