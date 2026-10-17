"""Each English movie row names the donor movie of its own slot.

Bug caught: a row shifted or swapped onto another scene's donor movie (the pins and hashes would still match the
disc, so only the scene length tells). Reads ASF headers only, from the stock game and the player's donor discs.
"""

from __future__ import annotations

import json
import os
import struct
import uuid
from pathlib import Path

import pytest

from wozmod.donors import DONORS
from wozmod.donors.extract import _open_disc
from wozmod import resources
from wozmod.movies import DATA

DISC_VARIABLES = {"wo2-x360": "WOZMOD_X360_ISO", "wo1-x360": "WOZMOD_WO1_X360_ISO"}
FILE_PROPERTIES = uuid.UUID("8cabdca1-a947-11cf-8ee4-00c00c205365").bytes_le
TOLERANCE = 0.5  # seconds; the donor cuts run within 0.35 s of stock, neighbouring scenes differ by far more


def _seconds(header: bytes) -> float:
    """The play length an ASF header gives: play duration (100 ns) less preroll (ms)."""
    at = header.index(FILE_PROPERTIES)
    play, _, preroll = struct.unpack_from("<QQQ", header, at + 64)
    return play / 1e7 - preroll / 1e3


def test_every_movie_row_matches_its_stock_scene_length():
    game = os.environ.get("WOZMOD_GAME")
    if not game or not Path(game, "movie").is_dir():
        pytest.skip("WOZMOD_GAME not set")
    rows = json.loads((resources.root() / DATA).read_text(encoding="utf-8"))["movies"]
    checked, wrong = 0, []
    for name in {row["donor"] for row in rows}:
        image = os.environ.get(DISC_VARIABLES[name], "")
        if not Path(image).is_file():
            continue
        with _open_disc(image, DONORS[name]) as disc:
            for row in (row for row in rows if row["donor"] == name):
                with disc.open(row["source"]) as member:
                    donor = _seconds(member.read(8192))
                stock = _seconds(Path(game, "movie", row["slot"] + ".wmv").read_bytes()[:8192])
                checked += 1
                if abs(donor - stock) > TOLERANCE:
                    wrong.append(f"{row['slot']} {stock:.2f} s, {row['source']} {donor:.2f} s")
    if not checked:
        pytest.skip("no donor disc image set")
    assert not wrong, wrong
