"""The PMF reader on synthetic program streams: two ATRAC3plus substreams whose frames span packets, between video
packets, padding and a system header, as the WO2 PSP movies lay them out."""

from __future__ import annotations

import random
import struct

import pytest

from wozmod.formats.pmf import PmfError, audio_tracks

WORD = 0x2845  # 44.1 kHz, stereo, 560-byte frames: the WO2 PSP movies' frame word


def _frames(rng: random.Random, count: int) -> list[bytes]:
    return [rng.randbytes(560) for _ in range(count)]


def _pes(code: int, header: bytes, payload: bytes) -> bytes:
    body = (bytes((0x81, 0x80, len(header))) + header if code == 0xBD else b"") + payload
    return b"\0\0\1" + bytes((code,)) + struct.pack(">H", len(body)) + body


def _pmf(streams: dict[int, list[bytes]], rng: random.Random, sizes: tuple[int, ...] = (),
         drop: frozenset[tuple[int, int]] = frozenset()) -> bytes:
    """A PSMF header, then packs: each substream's framed bytes cut into chunks regardless of the frame edges (of
    sizes in turn, else of 100-1500 bytes), the substreams' packets interleaved with video packets and padding.
    drop leaves out the packets of those (substream, chunk) pairs."""
    chunks = []
    for number, frames in streams.items():
        data = b"".join(b"\x0f\xd0" + struct.pack(">H", WORD) + bytes(4) + frame for frame in frames)
        at, k = 0, 0
        while at < len(data):
            size = sizes[k % len(sizes)] if sizes else rng.randrange(100, 1500)
            if (number, k) not in drop:
                chunks.append((number, data[at:at + size]))
            at, k = at + size, k + 1
    order = sorted(range(len(chunks)), key=lambda k: (k if chunks[k][0] == 0 else k - len(chunks) / 2) + rng.random())
    stream = bytearray()
    system = b"\0\0\1\xbb" + struct.pack(">H", 6) + bytes(6)
    for n, k in enumerate(order):
        number, chunk = chunks[k]
        stream += b"\0\0\1\xba" + bytes((0x44,)) + bytes(8) + bytes((0xF8 | n % 3,)) + bytes(n % 3)
        if n == 0:
            stream += system
        stream += _pes(0xE0, b"", rng.randbytes(rng.randrange(1, 2000)))
        header = (rng.randbytes(5), rng.randbytes(8), b"")[n % 3]  # a PTS, a PTS and its extension, or nothing
        stream += _pes(0xBD, header, bytes((number,)) + rng.randbytes(3) + chunk)
        if n % 4 == 0:
            stream += _pes(0xBE, b"", bytes(rng.randrange(0, 300)))
    stream += b"\0\0\1\xb9"
    return b"PSMF0015" + struct.pack(">II", 2048, len(stream)) + bytes(2032) + stream


def _data(riff: bytes) -> tuple[tuple, bytes]:
    """(channels, rate, block align) from the fmt chunk, and the data chunk's bytes."""
    fmt_size = struct.unpack_from("<I", riff, 16)[0]
    channels, rate, _, align = struct.unpack_from("<HIIH", riff, 22)
    data = 20 + fmt_size
    assert riff[data:data + 4] == b"data"
    return (channels, rate, align), riff[data + 8:data + 8 + struct.unpack_from("<I", riff, data + 4)[0]]


def test_each_substreams_frames_come_back_whole_and_in_order():
    # Bug: a frame split across packets loses or repeats bytes, the PES header length is miscounted (a PTS
    # extension's bytes join the audio), the substreams are swapped or merged, or a video or padding packet is
    # taken for audio.
    rng = random.Random(201)
    streams = {0: _frames(rng, 40), 1: _frames(rng, 37)}
    tracks = audio_tracks(_pmf(streams, rng))
    assert sorted(tracks) == [0, 1]
    for number, frames in streams.items():
        assert _data(tracks[number]) == ((2, 44_100, 560), b"".join(frames)), f"substream {number}"


def test_a_lost_audio_packet_stops_the_read():
    # Bug: only the first frame header is checked, so a packet missing from a damaged file shifts every later frame
    # and ffmpeg decodes noise into the movie. The two lost packets hold two frames' worth of bytes between them, so
    # the substream's length still divides into frames.
    rng = random.Random(202)
    with pytest.raises(PmfError, match="substream 0: frame header"):
        audio_tracks(_pmf({0: _frames(rng, 20), 1: _frames(rng, 20)}, rng, sizes=(300, 836, 1136),
                               drop=frozenset({(0, 3), (0, 7)})))
