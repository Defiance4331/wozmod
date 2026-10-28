"""Hash catalog and runtime table construction regressions."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from wozmod.text.build import TableBuildError, _load_data, build_table, table_index, table_pairs, table_parts
from synthetic import pe_image, script as synthetic_script
from wozmod.text.stock import resolve_stock_ref, stock_key, stock_strings

TABLE = Path(__file__).resolve().parents[1] / "data/text/table.json"
# The shipped table's rows: their count and the SHA-256 of their sorted keys, one per line.
ROW_COUNT = 23927
ROW_KEYS_SHA256 = "c42c606facb4804b0be6a52cdc8974e568fef45003b122ed2b463378c382d3eb"


def test_shipped_table_is_valid_and_keeps_every_row() -> None:
    # Bug: a hand edit to data/text/table.json drops, duplicates or rekeys a row, or breaks the schema; the
    # row's line stays Japanese, or the install fails on the player's machine. The build checks only at install.
    keys = sorted(row["key"] for row in _load_data(TABLE)["rows"])
    found = (len(keys), hashlib.sha256("\n".join(keys).encode()).hexdigest())

    assert found == (ROW_COUNT, ROW_KEYS_SHA256), f"rows added or removed; if on purpose, pin {found}"


def _token(role: str) -> bytes:
    return (chr(0x3042) * 2 + role).encode("cp932")


def _strings(*values: bytes) -> bytes:
    header = 4 + 4 * len(values)
    parts = [value + b"\0" for value in values]
    offsets = [header + sum(map(len, parts[:index])) for index in range(len(parts))]
    return struct.pack(f"<{len(values) + 1}I", len(values), *offsets) + b"".join(parts)


def _spans(parts: list[bytes], head: bytes = b"") -> bytes:
    at = len(head) + 8 * len(parts)
    table = bytearray()
    for part in parts:
        table.extend(struct.pack("<II", at, len(part)))
        at += len(part)
    return head + bytes(table) + b"".join(parts)


def _mesc(value: bytes) -> bytes:
    return b"MESC" + struct.pack("<III", 1, 17 + len(value), 16) + value + b"\0"


def _archive(directory: Path, number: int, payloads: dict[int, bytes]) -> None:
    index, body = bytearray(), bytearray()
    for ordinal in range(max(payloads) + 1):
        payload = payloads.get(ordinal, b"")
        index.extend(struct.pack("<III", len(payload), 0, 0))
        body.extend(payload + bytes(-len(payload) % 2048))
    (directory / f"LINKDATA{number}.IDX").write_bytes(index)
    (directory / f"LINKDATA{number}.BIN").write_bytes(body)


def _stock_pe(common: bytes, missing: bytes, message: bytes) -> bytes:
    image = pe_image(0x1E200, (b".text", 0x1E000, 0x2DE000, 0x1E000, 0x200), optional_size=0x60)
    cursor = 0x1600
    pointers = []
    for value in (common, missing, message, _token("scanner")):
        pointers.append(0x6DE000 + cursor - 0x200)
        image[cursor:cursor + len(value) + 1] = value + b"\0"
        cursor += len(value) + 1
    for index in range(911):
        struct.pack_into("<I", image, 0x6F0 + 4 * index, pointers[2])
    for index in range(890):
        struct.pack_into("<II", image, 0x18568 + 8 * index, pointers[index == 2], 0)
    for index in range(384):  # the weapons, 0x6FA5F8
        struct.pack_into("<II", image, 0x1C7F8 + 8 * index, pointers[0], 0)
    return bytes(image)


@pytest.fixture
def synthetic_stock(tmp_path: Path) -> tuple[Path, list[bytes]]:
    stock = tmp_path / "stock"
    stock.mkdir()
    roles = ["notice", "dialog", "caption", "event", "narration", "format", "quote-notice", "officer", "briefing", "name", "unmapped", "message", "scanner"]
    values = {role: _token(role) for role in roles}
    notices = _strings(b"32" + values["notice"])
    script = synthetic_script(notices, _strings(b"00" + values["dialog"] + b"@0001"))
    indexed = _token("indexed")
    event = bytes(12) + b"\xff" * 4 + struct.pack("<HH", 0x281, 32) + indexed + b"\0"
    event += bytes(-len(event) % 16) + b"\xff" * 4 + chr(0x3042).encode("cp932") + b"\0"
    voiced = _token("voiced")  # a WO2 event line with a voice: index 5, voice id 0xAF3 + 5
    event += bytes(-(len(event) - 12) % 16) + b"\xff" * 4 + struct.pack("<HH", 5, 32) + voiced + b"\0"
    _archive(stock, 2, {
        0: script,
        145: struct.pack("<III", 1, 1, 2) + values["caption"] + b"\0",
        177: event + b"\xff" * 4 + values["event"] + b"\0",
    })
    quote = _spans([
        _strings(b"33" + values["format"]),
        _strings(b"32" + values["quote-notice"]),
        _spans([_strings(b"00" + values["officer"] + b"@1234")]),
    ], struct.pack("<I", 3))
    _archive(stock, 1, {4071: _mesc(values["narration"]), 4084: quote, 4190: _mesc(values["briefing"])})
    (stock / "OROCHI_Z.exe").write_bytes(_stock_pe(values["name"], values["unmapped"], values["message"]))
    return stock, [*values.values(), values["officer"] + b"@1234", indexed, chr(0x3042).encode("cp932"), voiced,
                   voiced + b"@V2808", values["dialog"] + b"@0001@L0.0"]


def test_stock_catalog_does_not_drop_archive_keys_or_quote_voice_suffixes(synthetic_stock) -> None:
    # Bugs: a dropped archive key; a quote or stage line listed without its @NNNN voice suffix; an event line not
    # listed with its "@V" voice id, so a row keyed by an event line's voice cannot build; a spoken stage line not
    # listed with its stage and line, so the own row of a line that shares its string (voice slots) cannot build.
    stock, values = synthetic_stock
    catalog = stock_strings(stock)
    missing = [hashlib.sha256(value).hexdigest() for value in values
               if catalog.get(hashlib.sha256(value).hexdigest()) != value]
    assert missing == []


def test_stock_catalog_takes_program_strings_up_to_400_bytes(synthetic_stock) -> None:
    # Bug: the program-string scan's limit (psp_exe.EXE_STRING, 400 bytes) shrinks or is off by one, so a row keyed
    # by a long program string cannot build; or it grows past 400 and also takes the longer runs between NULs.
    stock, _ = synthetic_stock
    exe = bytearray((stock / "OROCHI_Z.exe").read_bytes())
    longest, over = b"X" * 400, b"Y" * 401
    exe[0x3000:0x3000 + 804] = b"\0" + longest + b"\0" + over + b"\0"  # an empty stretch of the synthetic .text
    (stock / "OROCHI_Z.exe").write_bytes(exe)
    catalog = stock_strings(stock)
    assert [hashlib.sha256(value).hexdigest() in catalog for value in (longest, over)] == [True, False]


def test_cp932_objective_aliases_do_not_drop_or_reorder_ibm_keys() -> None:
    row = {"ja": "1" + chr(0x7E8A) + "%s [10]", "en": "1Authored %s [10]"}
    index = table_index([row])
    keys = [b"1\xed\x40%s [10]", b"\xed\x40%s [10]", b"1\xfa\x5c%s [10]", b"\xfa\x5c%s [10]"]
    assert list(index.items()) == [(key, (row["en"].encode()[offset:], row))
                                   for key, offset in zip(keys, (0, 1, 0, 1), strict=True)]


def test_objective_alias_does_not_consume_a_literal_percent_argument() -> None:
    row = {"ja": "1%s item: %d / %02d (%%s)", "en": "1Authored %s: %d / %02d (%%s)", "order": [1, 2, 3]}
    assert table_pairs([row]) == {
        row["ja"].encode(): row["en"].encode(), row["ja"][1:].encode(): row["en"][1:].encode(),
    }
    assert table_pairs([{"ja": "Source %s %d", "en": "Authored %s", "order": [1]}]) == {b"Source %s %d": b"Authored %s"}


@pytest.mark.parametrize("form", ["% d", "%*d", "%ld", "%lld", "%I64d", "%f", "%u", "%03d", "%", "%q"])
def test_unsupported_printf_form_cannot_hide_a_consumed_argument(form: str) -> None:
    row = {"ja": "Source: " + form, "en": "Authored: " + form}
    key = hashlib.sha256(row["ja"].encode()).hexdigest()
    with pytest.raises(TableBuildError) as error:
        table_pairs([row])
    assert key in str(error.value) and repr(form) in str(error.value)


@pytest.mark.parametrize(("stock", "english", "order", "problem"), [
    ("Source %s %d", "Authored %d %s", None, "no argument order"),  # the order is the row's to state
    ("Source %s %d", "Authored %d %s", [1, 2], "format arguments differ"),  # a %s would print a number
    ("Source %d %d", "Authored %d", [1, 1], "does not fit"),
    ("Source %d %d", "Authored %d %d", [2, 3], "does not fit"),
    ("Source %s", "Authored %s %d", None, "format arguments differ"),
])
def test_reordered_or_added_printf_arguments_cannot_enter_the_table(stock, english, order, problem) -> None:
    # Bug: an English sentence prints the game's arguments in another order, or one it never passes, unnoticed.
    with pytest.raises(TableBuildError, match=problem):
        table_pairs([{"ja": stock, "en": english, **({"order": order} if order else {})}])


@pytest.mark.parametrize(("stock", "english", "problem"), [
    ("Stock", "Authored\ttext", "control"),
    ("Stock", "Authored [x]", "control"),
    ("Stock", "Authored " + chr(0xFF66), "high byte"),
    ("1%s item", "Authored %s item", "objective count"),
    ("Stock " + chr(0xFF05), "Authored percent", "literal percent"),
    (bytes((0xF0, 0x40)).decode("cp932") + "Stock", "Authored", "shape"),
    ("Stock", "Authored " + bytes((0xF0, 0x55, 0xF0, 0x56)).decode("cp932") + ".", "icon"),
])
def test_undrawable_row_cannot_enter_the_runtime_table(stock: str, english: str, problem: str) -> None:
    with pytest.raises(TableBuildError, match=problem):
        table_pairs([{"ja": stock, "en": english}])


def test_icon_check_does_not_read_the_start_icon_argument_or_a_trail_byte_as_an_icon() -> None:
    start = bytes((0xF0, 0x48, 0xF0, 0x49)).decode("cp932")
    english = start + "Authored " + bytes((0x88, 0xF0)).decode("cp932") + "V" + bytes((0xF0, 0x55)).decode("cp932")
    assert table_pairs([{"ja": start + "Stock", "en": english}]) == {
        (start + "Stock").encode("cp932"): english.encode("cp932")}


def test_part_override_gives_only_its_own_slot_where_one_stock_string_fills_several() -> None:
    # Bug: an override keyed by the stock string (or by the wrong table, or shifted by the name table's length) also
    # renames every other name part or weapon that shares the string, or misses its own slot.
    common = _token("common")
    parts, overridden = table_parts(_stock_pe(common, _token("missing"), _token("message")), {common: b"Common"}, [
        {"table": "names", "index": 1, "en": "Override"}, {"table": "names", "index": 3, "en": ""},
        {"table": "weapons", "index": 5, "en": "Fixture weapon"},
    ])
    assert parts == [b"Common", b"Override", None, *([b"Common"] * 887), *([b"Common"] * 5), b"Fixture weapon",
                     *([b"Common"] * 378)]
    assert overridden == [1, 895]


class _SyntheticResolver:
    def resolve_row(self, row: dict) -> str:
        return row["en"] if "en" in row else {1: "Resolved pointer"}[row["ref"][1]]


def _table_values(blob: bytes) -> tuple[dict[bytes, bytes], list[bytes | None]]:
    mask, names_at = struct.unpack_from("<II", blob)

    def value(at: int) -> bytes:
        return blob[at:blob.index(b"\0", at)]

    pairs = {value(key): value(english) for _, key, english in struct.iter_unpack("<III", blob[8:8 + 12 * (mask + 1)]) if key}
    count = struct.unpack_from("<I", blob, names_at)[0]
    names = [value(at) if at else None for at in struct.unpack_from(f"<{count}I", blob, names_at + 4)]
    return pairs, names


def test_hashed_builder_does_not_drop_a_pointer_row_or_shift_name_overrides(synthetic_stock, tmp_path: Path) -> None:
    stock, _ = synthetic_stock
    executable = stock / "OROCHI_Z.exe"
    old_exe = executable.read_bytes()
    substring = _token("substring")
    executable.write_bytes(old_exe + b"prefix" + substring + b"suffix\0")
    authored = {"key": hashlib.sha256(_token("message")).hexdigest(), "source": "authored", "en": "Authored message"}
    pointer = {"key": hashlib.sha256(_token("name")).hexdigest(), "source": "wo1-x360", "ref": ["wo1-x360", 1]}
    view = {"key": hashlib.sha256(substring).hexdigest(), "source": "authored", "en": "Authored substring", "key_ref": ["exe", len(old_exe) + 6, len(substring)]}
    data = tmp_path / "data"
    data.mkdir()
    (data / "table.json").write_text(json.dumps({"version": 1, "rows": [pointer, authored, view], "parts": [{"table": "names", "index": 1, "source": "authored", "en": "Override"}]}))
    output = tmp_path / "built" / "table.bin"
    result = build_table(stock, _SyntheticResolver(), data, output)
    pairs, names = _table_values(output.read_bytes())
    assert pairs == {_token("name"): b"Resolved pointer", _token("message"): b"Authored message", substring: b"Authored substring"}
    assert names[:890] == [b"Resolved pointer", b"Override", None, *([b"Resolved pointer"] * 887)]
    assert result == {"rows": 3, "pairs": 3, "parts": 1273, "overrides": 1, "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def test_voiced_line_keeps_its_own_english_where_another_voice_shares_its_text(synthetic_stock, tmp_path: Path) -> None:
    # Bug: the catalog lists a spoken stage line only without its @NNNN voice suffix, so a row keyed by the voiced
    # line cannot build. A PSP-voiced Dream line whose text an Xbox 360-voiced line shares then shows the Xbox
    # wording over the PSP voice. The runtime looks the line up with its suffix first, then the text alone.
    stock, _ = synthetic_stock
    text, voiced = _token("dialog"), _token("dialog") + b"@0001"
    rows = [{"key": hashlib.sha256(text).hexdigest(), "source": "authored", "en": "Shared wording"},
            {"key": hashlib.sha256(voiced).hexdigest(), "source": "authored", "en": "This voice's wording"}]
    data = tmp_path / "table.json"
    data.write_text(json.dumps({"version": 1, "rows": rows, "parts": []}))
    output = tmp_path / "table.bin"
    build_table(stock, _SyntheticResolver(), data, output)
    pairs, _ = _table_values(output.read_bytes())
    assert pairs == {text: b"Shared wording", voiced: b"This voice's wording"}


@pytest.mark.parametrize("fault", ["hash", "duplicate-row", "source", "name-index", "duplicate-name", "version"])
def test_bad_schema_cannot_drop_or_overwrite_a_row(fault: str, tmp_path: Path) -> None:
    row = {"key": "0" * 64, "source": "authored", "en": "Authored"}
    payload = {"version": 1, "rows": [row], "parts": []}
    if fault == "hash":
        row["key"] = "0" * 63
    elif fault == "duplicate-row":
        payload["rows"].append(dict(row))
    elif fault == "source":
        row["source"] = "absent"
    elif fault == "name-index":
        payload["parts"] = [{"table": "names", "index": 890, "source": "authored", "en": "Authored"}]
    elif fault == "duplicate-name":
        payload["parts"] = [{"table": "names", "index": 1, "source": "authored", "en": "Authored"}] * 2
    else:
        payload["version"] = 2
    data = tmp_path / "data.json"
    data.write_text(json.dumps(payload))
    output = tmp_path / "table.bin"
    with pytest.raises(TableBuildError):
        build_table(tmp_path / "absent", _SyntheticResolver(), data, output)
    assert not output.exists()


def test_missing_stock_hash_cannot_silently_drop_a_translation(synthetic_stock, tmp_path: Path) -> None:
    stock, _ = synthetic_stock
    key = "0" * 64
    data = tmp_path / "table.json"
    data.write_text(json.dumps({"version": 1, "rows": [{"key": key, "source": "authored", "en": "Authored"}], "parts": []}))
    output = tmp_path / "table.bin"
    with pytest.raises(TableBuildError, match=key):
        build_table(stock, _SyntheticResolver(), data, output)
    assert not output.exists()


def test_stock_substring_reference_cannot_use_a_shifted_offset_or_length(tmp_path: Path) -> None:
    token = _token("view")
    key = hashlib.sha256(token).hexdigest()
    (tmp_path / "OROCHI_Z.exe").write_bytes(b"prefix" + token + b"suffix" + bytes((0xFA, 0x5C)))
    assert resolve_stock_ref(tmp_path, ["exe", 6, len(token)], key) == token
    canonical = bytes((0xED, 0x40))
    assert resolve_stock_ref(tmp_path, ["exe", 12 + len(token), 2], hashlib.sha256(canonical).hexdigest()) == canonical
    for reference in (["exe", 7, len(token)], ["exe", 6, len(token) + 1], ["exe", 6, 999], ["other", 6, len(token)], ["exe", -1, len(token)]):
        with pytest.raises(TableBuildError, match=key):
            resolve_stock_ref(tmp_path, reference, key)



def test_stock_key_joins_both_cp932_spellings_and_skips_bytes_that_are_not_cp932() -> None:
    """Bug: the key hashes raw bytes, so the IBM spelling of a character misses its row, or a stray non-CP932 run
    (most program-string matches in the stock exe) aborts the stock catalog instead of being skipped."""
    nec = bytes((0xED, 0x40))
    assert [stock_key(nec), stock_key(bytes((0xFA, 0x5C))), stock_key(b"\x81")] == [
        (hashlib.sha256(nec).hexdigest(), nec), (hashlib.sha256(nec).hexdigest(), nec), None]

@pytest.mark.skipif(not os.environ.get("WOZMOD_GAME"), reason="set WOZMOD_GAME to the stock game")
@pytest.mark.slow_first
def test_every_stock_text_line_has_a_row_or_a_reviewed_reason_to_stay_japanese() -> None:
    # Bug: a stock line silently ships untranslated: a dropped or rekeyed row, a source the table never covered, or
    # a source the inventory stops reading (which would leave nothing to check). A reviewed key that later gets a
    # row, or leaves the stock game, is stale and would hide the next gap.
    from wozmod.text.inventory import inventory

    keys = {row["key"] for row in json.loads(TABLE.read_text())["rows"]}
    reviewed = json.loads((TABLE.parent / "left_japanese.json").read_text())["keys"]
    allowed = {entry["key"] for entry in reviewed}
    found = inventory(Path(os.environ["WOZMOD_GAME"]))
    stock = {stock_key(text)[0]: source for source, texts in found.items() for text in texts}

    assert set(found) == {"notices", "spoken", "captions", "events", "mesc 4071", "mesc 4073", "mesc 4103",
                          "mesc 4105", "briefings", "quote formats", "officer quotes", "exe messages"}
    assert all(entry["reason"].strip() and entry["source"] == stock.get(entry["key"]) for entry in reviewed)
    assert sorted((source, key) for key, source in stock.items() if key not in keys | allowed) == []
    assert sorted(allowed & keys | allowed - stock.keys()) == []
