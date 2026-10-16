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
    import hashlib
    import unicodedata

    from wozmod.text.psp_exe import EXE_STRING
    from wozmod.text.resolver import DonorResolver

    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))
    # The gallery titles are program strings; scanning only the program keeps this test fast.
    stock = {}
    for match in EXE_STRING.finditer((Path(os.environ["WOZMOD_GAME"]) / "OROCHI_Z.exe").read_bytes()):
        try:
            text = match.group().decode("cp932")
        except UnicodeDecodeError:
            continue
        stock[hashlib.sha256(text.encode("cp932")).hexdigest()] = text
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
