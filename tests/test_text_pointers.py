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
