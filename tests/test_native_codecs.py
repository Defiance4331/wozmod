"""The native codecs give the same bytes as the Python reference codecs.

Skipped when the optional `wozmod-native` package is not installed. One bug
turns these red: a native off-by-one that no hand-derived Python test sees, such as
a source 2049 bytes back taken as legal, a farthest legal source dropped, a run
cut at the wrong length, a tie in the nearest colour or alpha level broken the
wrong way, or an edge block that does not repeat the last row and column.
"""

from __future__ import annotations

import random

import pytest

from wozmod.formats import linkdata, textures

native = pytest.importorskip("wozmod_native")

WINDOW = linkdata.MAX_BACK_REFERENCE_DISTANCE


def _lzp2_inputs() -> list[tuple[str, bytes]]:
    rng = random.Random(20261011)
    cases: dict[str, bytes] = {"empty": b"", "one": b"a", "two": b"ab", "three": b"abc"}
    for length in (3, 4, 5, 17, 18, 19, 20, 0x3F03 - 1, 0x3F03, 0x3F03 + 1, 0x3F03 + 5):
        cases[f"run{length}"] = b"\x07" * length
        cases[f"run{length}-tail"] = b"xy" + b"\x00" * length + b"xy"
    noise = bytes(rng.randrange(256) for _ in range(5000))
    for gap in (WINDOW - 1, WINDOW, WINDOW + 1):  # the source sits exactly `gap` bytes back
        filler = bytes(rng.randrange(256) for _ in range(gap - 5))
        cases[f"window{gap}"] = b"HELLO!" + filler + b"HELLO!" + noise[:40]
        cases[f"window{gap}-long"] = b"0123456789ABCDEFGH" + filler[:gap - 18] + b"0123456789ABCDEFGH"
    for literal in (62, 63, 64, 65, 126, 127, 128):
        cases[f"literal{literal}"] = bytes(rng.sample(range(256), literal)) + b"\x00" * 4 + b"end"
    for period in (1, 2, 3, 5):  # the copy overlaps its own output
        cases[f"period{period}"] = bytes(range(65, 65 + period)) * 40
    cases["ladder"] = b"abcdefgh" + b"abcdefgz" + b"abcdefg" + b"abcdefghij"  # longest, then nearest, source
    for alphabet in (2, 3, 4, 16):
        for size in (7, 100, 3000, 20000):
            cases[f"alpha{alphabet}-{size}"] = bytes(rng.randrange(alphabet) for _ in range(size))
    for number in range(60):  # noise with planted copies from inside and outside the window
        data = bytearray(rng.randbytes(rng.randrange(1, 6000)))
        for _ in range(rng.randrange(1, 30)):
            if len(data) < 10:
                break
            start = rng.randrange(len(data) - 4)
            length = rng.randrange(3, 30)
            at = rng.randrange(len(data))
            data[at:at] = data[start:start + length]
        cases[f"planted{number}"] = bytes(data)
    return list(cases.items())


LZP2_INPUTS = _lzp2_inputs()


def test_native_lzp2_encoder_gives_the_python_stream_on_every_input() -> None:
    wrong = [name for name, data in LZP2_INPUTS if native.lzp2_encode_stream(data) != linkdata._encode_stream(data)]

    assert wrong == []


def test_native_lzp2_decoder_gives_the_python_output_and_the_same_errors() -> None:
    wrong = []
    for name, data in LZP2_INPUTS:
        stream = linkdata._encode_stream(data)
        if native.lzp2_decode_stream(stream, len(data)) != linkdata._decode_stream(stream, len(data)):
            wrong.append(name)
    assert wrong == []

    good = linkdata._encode_stream(b"abcabcabcabc" + bytes(range(70)))
    size = 12 + 70
    broken = {
        "size": (good, size + 1),
        "padding": (good[:-1] + b"\x01", size),
        "no end": (good.rstrip(b"\0")[:-1], size),
        "literal": (b"\x05ab", 5),
        "repeat": (b"\x40\x00", 4),
        "back-reference": (b"\x80", 3),
        "before start": (b"\x01a\x88\x05\0", 4),
        "too long": (b"\x01a\x40\x10a\0", 3),
    }
    for name, (stream, expected) in broken.items():
        with pytest.raises(ValueError) as python:
            linkdata._decode_stream(stream, expected)
        with pytest.raises(ValueError) as fast:
            native.lzp2_decode_stream(stream, expected)
        assert str(fast.value) == str(python.value), name


def _dxt5_images() -> list[tuple[str, bytes, int, int]]:
    rng = random.Random(20261012)
    images = []

    def add(name: str, width: int, height: int, pixel) -> None:
        images.append((name, b"".join(bytes(pixel(x, y)) for y in range(height) for x in range(width)), width, height))

    add("one colour", 8, 8, lambda x, y: (200, 100, 50, 255))
    add("one colour, no alpha", 4, 4, lambda x, y: (0, 0, 0, 0))
    add("white", 4, 4, lambda x, y: (255, 255, 255, 255))  # equal endpoints at the top of the range
    add("equal alpha", 4, 4, lambda x, y: (x * 60, y * 60, 9, 128))
    add("16 colours", 4, 4, lambda x, y: (x * 60 + 3, y * 60 + 5, (x ^ y) * 80, 255))
    add("16 colours, 16 alphas", 4, 4, lambda x, y: (x * 60 + 3, y * 60 + 5, (x ^ y) * 80, x * 4 + y * 16 + 1))
    add("faint corner", 4, 4, lambda x, y: (250, 10, 10, 255) if x + y < 3 else (5, 5, 240, 3 if (x + y) % 2 else 0))
    for width, height in ((1, 1), (2, 5), (5, 3), (7, 9), (13, 2), (4, 1)):  # edge blocks repeat the last row and column
        add(f"edge {width}x{height}", width, height, lambda x, y: ((x * 37) % 256, (y * 91) % 256, (x * y * 13) % 256, 255 - x * y))
    for number in range(40):
        width, height = rng.randrange(1, 30), rng.randrange(1, 30)
        colours = rng.randrange(1, 17)
        palette = [(rng.randrange(256), rng.randrange(256), rng.randrange(256), rng.choice((0, 1, 127, 128, 200, 255, rng.randrange(256)))) for _ in range(colours)]
        grid = [[rng.choice(palette) for _ in range(width)] for _ in range(height)]
        add(f"random{number} {width}x{height} {colours} colours", width, height, lambda x, y, g=grid: g[y][x])
    for number in range(10):
        width, height = rng.randrange(1, 20), rng.randrange(1, 20)
        data = rng.randbytes(width * height * 4)
        images.append((f"noise{number} {width}x{height}", data, width, height))
    return images


def test_native_dxt5_encoder_gives_the_python_blocks_on_every_image() -> None:
    wrong = [name for name, rgba, width, height in _dxt5_images()
             if native.dxt5_encode(rgba, width, height) != textures._encode_dxt5_python(rgba, width, height)]

    assert wrong == []
