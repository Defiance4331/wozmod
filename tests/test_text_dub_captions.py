import json
import struct

import pytest

from wozmod.formats.scripts import caption_cues
from wozmod.text import DonorResolver
from wozmod.text.dub_captions import DubCaptionError, build_dub_schedules


def _french(times: list[tuple[int, int]]) -> bytes:
    """A French schedule in the stock layout, its texts our own placeholders."""
    return (struct.pack("<I", len(times)) + b"".join(struct.pack("<II", *t) for t in times)
            + b"".join(b"cue %d\0" % i for i in range(len(times))))


def _build(tmp_path, cues: list[str]):
    donors = tmp_path / "donors"
    (donors / "wo1-x360/linkdata_fns").mkdir(parents=True)
    # 25 ticks a second: 2.0 to 3.6 s and 10.0 to 12.5 s.
    (donors / "wo1-x360/linkdata_fns/370.bin").write_bytes(_french([(50, 90), (250, 312)]))
    data = tmp_path / "dub_captions.json"
    data.write_text(json.dumps({"movies": [
        {"slot": "movie103", "entry": 172, "timing": ["wo1-x360", "fns", 370], "cues": cues}]}))
    build_dub_schedules(DonorResolver(donors), data, tmp_path / "out")
    return tmp_path / "out/wozmod/voice/entries"


def test_each_caption_takes_its_french_cue_time_on_the_stock_clock(tmp_path):
    # Bug: the 25-a-second French counts are copied unscaled (captions run 20% early), or the file is named by the
    # French entry or the bare LINKDATA2 entry instead of the runtime's key (4251 + 172), so the game never reads it.
    folder = _build(tmp_path, ["Our first line.", "Our second line."])
    assert [p.name for p in folder.iterdir()] == ["4423.bin"]
    # 2.0 s is 59.94 stock ticks, 3.6 s 107.89, 10.0 s 299.7, 12.48 s 374.03.
    assert caption_cues((folder / "4423.bin").read_bytes()) == [
        (60, 108, b"Our first line."), (300, 374, b"Our second line.")]


def test_a_caption_count_unlike_the_french_cues_is_refused(tmp_path):
    # Bug: zip() drops the extra caption, or a missing one shifts every later line onto the wrong speech.
    with pytest.raises(DubCaptionError, match="movie103"):
        _build(tmp_path, ["Only one line."])
