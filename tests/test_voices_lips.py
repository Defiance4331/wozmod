import struct

from wozmod.formats.audio import WO1_EVENT_VOICES, WO2_EVENT_VOICES, build_synthetic_kovs_record
from wozmod.voices.lips import (
    TAG, X360_TAG, donor_lips, english_lips, lip_bank, stock_lips, wo1_fits, x360_fits,
)


REST = b"\x00\x37\x64\x00"


def _block(order, length, tracks):
    tag = TAG if order == "<" else X360_TAG
    keys = b"".join(bytes(key) for track in tracks for key in track)
    return tag + struct.pack(f"{order}I4HI", length, *map(len, tracks), 0x12, 0x7E41) + REST + keys


def _event(order, lines):
    """Line records at 12 mod 16, each followed by its block; a record's text may be empty."""
    data = bytearray(12)
    for index, text, block in lines:
        data += b"\xff" * 4 + struct.pack(f"{order}HH", index, 16) + text.ljust(12, b"\0") + block
        data += bytes(-(len(data) - 12) % 16)
    return bytes(data)


def _clip(frames):
    return build_synthetic_kovs_record(96, sample_count=frames * 1600)


def test_each_english_clip_gets_the_block_its_donor_plays_with_it_or_one_stretched_to_it():
    # Catches: a block read from the wrong offset, X360 lengths, counts or the header words after them left
    # big-endian, a line with no text
    # dropped, a donor line paired with the wrong clip, one donor's fit rule applied to the other, and a
    # stretch that misses the clip length.
    tracks = [[(0, 127, 10)], [(55, 127, 4), (60, 127, 6)], [(100, 127, 10)]]
    stretched = [[(0, 127, 30)], [(55, 127, 12), (60, 127, 18)], [(100, 127, 30)]]
    x360 = _event(">", [(3, b"donor", _block(">", 20, tracks)), (4, b"", _block(">", 10, tracks))])
    wo1 = _event(">", [(3, b"donor", _block(">", 20, tracks))])
    stock = _event("<", [(5, b"stock", _block("<", 10, tracks))])
    clips = {100: _clip(20), 101: _clip(30), 200: _clip(30), 201: _clip(30)}
    sources = {100: "wo2-x360", 101: "wo2-x360", 200: "wo1-x360", 201: "wo1-x360"}

    lips, tiers = english_lips(clips, sources, stock_lips([(196, stock)]), {
        "wo2-x360": ({100: 1401, 101: 1402}, donor_lips([x360], ">", *WO2_EVENT_VOICES[1:]), x360_fits),
        "wo1-x360": ({200: 960, 201: 961}, donor_lips([wo1], ">", *WO1_EVENT_VOICES[1:]), wo1_fits),
    })

    assert lips == {
        100: _block("<", 20, tracks),       # X360 line 3 plays clip 1398 + 3 and runs the whole clip
        101: _block("<", 30, stretched),    # X360 line 4 shipped a block a third the clip's length
        200: _block("<", 20, tracks),       # WO1 line 3 plays record 957 + 3; its block ends with the speech
        201: _block("<", 30, stretched),    # no WO1 line plays record 961: the stock block for voice 201
    }
    assert tiers == {"donor": 2, "stretched": 2}
    bank = lip_bank(lips)
    for voice, block in lips.items():
        offset, size = struct.unpack_from("<II", bank, 4 + 8 * voice)
        assert bank[offset:offset + size] == block
