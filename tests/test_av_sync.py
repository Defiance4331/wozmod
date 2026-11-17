"""Putting a harness video on the game's clock (tools/av_sync.py): reading the Present number painted in each frame's
corner, and taking each frame to its logged Present."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import av_sync  # noqa: E402


def painted(n: int, white: int = 235, black: int = 16) -> bytes:
    """The grey row through the corner cells as english_runtime.c's av_before paints Present n: a white cell, its 12
    low bits low first, a black cell, each AV_CELL wide (levels as H.264 leaves them)."""
    cells = [True] + [bool(n >> i & 1) for i in range(12)] + [False]
    return b"".join(bytes([white if on else black]) * av_sync.CELL for on in cells) + bytes(24)


@pytest.mark.parametrize("n", [1, 2, 0x800, 4095, 4096, 4097, 123456])
def test_a_painted_present_number_reads_back(n):
    # Bug: a bit order or cell offset off by one names every frame's Present wrong, so the video is retimed to the
    # wrong Presents by a whole number of frames.
    row = painted(n)
    assert av_sync.corner_code(row, len(row)) == n % 4096


def test_a_frame_without_the_reference_cells_names_no_present():
    # Bug: a frame from before the probe painted (or a scene cut to white) read as a Present number.
    row = bytes([235]) * (av_sync.CELLS * av_sync.CELL)
    assert av_sync.corner_code(row, len(row)) == -1


def test_frames_take_their_presents_across_the_code_wrap_and_skipped_presents():
    # Bug: the 12-bit code taken to the wrong Present after it wraps at 4096, or a misread code dragging every later
    # frame onto Presents a wrap away. Recordings skip Presents (9 to 75 a run) and may show one twice.
    presents = {n: 1_000_000 * n for n in range(4090, 8300)}  # long enough that a misread could land on a logged one
    codes = [4090 % 4096, 4091, 4093 % 4096, 4093, 4095, 4096 % 4096, 4098 % 4096, 1234, 4100 % 4096, -1, 4102 % 4096]
    numbers = av_sync.frame_presents(codes, presents)
    assert numbers == [4090, 4091, 4093, 4093, 4095, 4096, 4098, None, 4100, None, 4102]
    times, filled = av_sync.frame_times(numbers, presents)
    assert filled == 2
    assert times[7] == (presents[4098] + presents[4100]) // 2 and times[9] == (presents[4100] + presents[4102]) // 2
    assert times[:2] == [presents[4090], presents[4091]]


def test_a_recording_that_starts_seconds_into_the_probe_finds_its_first_present():
    # Bug: the first frame held to the misread-bit bound from the log's first Present, so a recording started 216
    # Presents into the probe named no frame at all and could not be retimed (run 2026-11-17_0143-lav-v1).
    presents = {n: n for n in range(1, 2103)}
    numbers = av_sync.frame_presents([216, 217, 219, -1, 220], presents)
    assert numbers == [216, 217, 219, None, 220]

