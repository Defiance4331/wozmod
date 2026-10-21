from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

TABLE = Path(__file__).resolve().parents[1] / "data/text/table.json"

# Stock strings that are themselves placeholders or developer markers, so a placeholder translation fits them.
PLACEHOLDER_STOCK = {
    "0ebb59da0ccaa5fe11a50907daca4b979e86cdabdb8c5278661bb35d4b43ef0a",
    "6dbcd5842497303708b440bc858873e49e09fc3081965e5704721f243099de0b",
    "b327104257783da4ad4b3133bf928bd235e1789bbb58ea0fa4a34122248a9835",
    "bd99a376ca4b899232a0492066e1f0fb7852df016bf4d0657b84fd3d423a9eab",
}


@pytest.mark.skipif(not os.environ.get("WOZMOD_DONORS"), reason="set WOZMOD_DONORS to extracted donor folders")
def test_no_pointer_lands_on_a_donor_placeholder_record():
    # Catches a row pointed at a donor's unused placeholder record, which the game then shows as the English.
    from wozmod.text.resolver import DonorResolver

    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))
    placeholders = {row["key"] for row in json.loads(TABLE.read_text())["rows"] if "ref" in row
                    and re.fullmatch(r"\s*\d*\s*dummy\s*", resolver.resolve_row(row), re.IGNORECASE)}
    assert placeholders - PLACEHOLDER_STOCK == set()


@pytest.mark.skipif(not os.environ.get("WOZMOD_DONORS"), reason="set WOZMOD_DONORS to extracted donor folders")
def test_every_string_pointer_starts_at_a_donor_string():
    # Catches a pointer shifted into the middle of a donor string, or into another language's copy of it, which
    # still resolves to plausible text. A string starts after the NUL that ends the one before it, or in an
    # archive entry after the ffffffff marker that opens its record (an event line's after its id and length).
    from wozmod.text.resolver import DonorResolver

    resolver, mid = DonorResolver(Path(os.environ["WOZMOD_DONORS"])), []
    for row in json.loads(TABLE.read_text())["rows"]:
        for ref in [row.get("ref"), *(edit[2] for edit in row.get("edits", []) if isinstance(edit[2], list))]:
            match ref:
                case [donor, "pe", int(at)]:
                    before = resolver.program(donor)[:at][-1:]
                case [donor, "ens" | "fns", int(entry), "cstr", int(at)]:
                    before = resolver.entry(donor, entry)[:at][-8:]
                case _:
                    continue
            if at and not (before.endswith(b"\0") or b"\xff" * 4 in (before[-4:], before[-8:-4])):
                mid.append(f"{row['key'][:12]} {ref}")
    assert mid == []


# The SHA-256 of the text each row showed before an edit pointer replaced its typed-in words.
EDIT_POINTER_TEXT = {
    "5c409fd1bcfef11d44442680292fe620a0597231c48aea6e10cacccadf4d69f0":
        "b61c47aa06fbfeeb47444ee519ea51e7acffcc685752f948eb5610305b6a8ad1",
}


@pytest.mark.skipif(not os.environ.get("WOZMOD_DONORS"), reason="set WOZMOD_DONORS to extracted donor folders")
def test_rows_that_insert_a_donor_string_read_as_they_did():
    # Catches an edit pointer at the wrong string, or an insert on the wrong side of a space: the screen would show
    # different text from the words the pointer replaced.
    import hashlib

    from wozmod.text.resolver import DonorResolver

    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))
    rows = {row["key"]: row for row in json.loads(TABLE.read_text())["rows"]
            if any(isinstance(edit[2], list) for edit in row.get("edits", []))}
    assert set(rows) == set(EDIT_POINTER_TEXT)
    built = {key: hashlib.sha256(resolver.resolve_row(row).encode()).hexdigest() for key, row in rows.items()}
    assert built == EDIT_POINTER_TEXT


@pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                    reason="set WOZMOD_DONORS and WOZMOD_GAME")
def test_the_real_table_builds_from_the_shipped_rows(tmp_path):
    # Catches a row whose donor text the renderer cannot draw or whose stock string is gone, which only the
    # build from real files sees.
    from wozmod.text.build import build_table
    from wozmod.text.resolver import DonorResolver

    built = build_table(Path(os.environ["WOZMOD_GAME"]), DonorResolver(Path(os.environ["WOZMOD_DONORS"])), TABLE,
                        tmp_path / "table.bin")
    assert built["rows"] == len(json.loads(TABLE.read_text())["rows"])


# Western number minus Japanese number. The first Dynasty Warriors was a fighting game, so the Japanese
# musou series counts one behind it; Samurai Warriors kept Japan's numbers.
SERIES_OFFSET = {"Dynasty Warriors": 1, "Samurai Warriors": 0}


@pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                    reason="set WOZMOD_DONORS and WOZMOD_GAME")
def test_series_titles_use_the_western_number():
    # Catches a title that carries the Japanese series number into English, such as an edit that renumbers
    # the official title down by one or a pointer paired to the title with the same digit.
    import unicodedata

    from wozmod.text.psp_exe import EXE_STRING
    from wozmod.text.resolver import DonorResolver
    from wozmod.text.stock import stock_key

    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))
    # The gallery titles are program strings; scanning only the program keeps this test fast.
    stock = {}
    for match in EXE_STRING.finditer((Path(os.environ["WOZMOD_GAME"]) / "OROCHI_Z.exe").read_bytes()):
        if keyed := stock_key(match.group()):
            stock[keyed[0]] = keyed[1].decode("cp932")
    wrong, checked = [], 0
    for row in json.loads(TABLE.read_text())["rows"]:
        title = re.match(rf"({'|'.join(SERIES_OFFSET)})(?: (\d))?\b", resolver.resolve_row(row))
        if title is None:
            continue
        # The stock title's number ends its first word; a title with none is the first game.
        japanese = unicodedata.digit(stock[row["key"]].split()[0][-1], 1)
        checked += 1
        if int(title[2] or 1) != japanese + SERIES_OFFSET[title[1]]:
            wrong.append((row["key"], title[0]))
    assert checked and wrong == []
