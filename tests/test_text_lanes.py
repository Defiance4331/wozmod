"""docs/design-language.md, Text lanes: every free-text lane the runtime sets (wozmod.runtime.text_lanes) wraps at
the narrowest of the art behind it, the safe area and the reading measure, and at no less."""

from __future__ import annotations

import json
import re
from pathlib import Path

from wozmod.runtime import latin_font, text_lanes

TABLE = Path(__file__).resolve().parents[1] / "data" / "text" / "table.json"


def test_every_lane_wraps_at_its_full_lane():
    # Red for a lane past its art, the safe area or the measure, and for one that wraps short of them.
    bad = []
    for lane in text_lanes.LANES:
        if lane.width != lane.lane():
            bound = min(lane.bounds(), key=lane.bounds().get)
            bad.append(f"{lane.element}: wraps at {lane.width}; its lane is {lane.lane()} ({bound}; {lane.bounds()})")
    assert not bad, "\n".join(bad)


def test_measure_is_75_characters_of_english():
    # Red when the measure's mean advance no longer matches English text in the game's font: a font whose letters
    # grew would leave 75 characters wider than MEASURE allows.
    rows = json.loads(TABLE.read_text(encoding="utf-8"))["rows"]
    sentences = [re.sub(r"\[..|\]", "", row["en"]).replace("\n", " ") for row in rows
                 if row["source"] == "authored" and isinstance(row.get("en"), str) and len(row["en"].split()) >= 6]
    sentences = [s for s in sentences if all(c in latin_font.LATIN_CHARS for c in s)]
    mean = sum(map(latin_font.width, sentences)) / sum(map(len, sentences))
    assert abs(text_lanes.MEAN_ADVANCE - mean) < 0.05, mean
