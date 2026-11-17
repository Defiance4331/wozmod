"""The audio tracks of a PSP movie (PMF): each ATRAC3plus substream as a RIFF that ffmpeg decodes.

A PMF is a 2048-byte "PSMF" header, then an MPEG-2 program stream. Audio sits in private stream 1 (PES id 0xBD):
each packet's payload starts with four bytes, the first of them the substream (WO2 PSP: 0 English, 1 Japanese).
A substream's bytes, joined across its packets, are ATRAC3plus frames, each after an 8-byte header: 0F D0, a
big-endian word that packs the rate, channels and frame size as OMA files do (ffmpeg's oma.c), then four bytes.
A frame can span packets. ffmpeg reads none of this: it shows a PMF's video only.
"""

from __future__ import annotations

import struct

from wozmod.formats.audio import ATRAC3PLUS_DECODER_DELAY, ATRAC3PLUS_GUID

HEADER = 2048  # the PSMF header; the program stream starts after it
FRAME_HEADER = 8
FRAME_SAMPLES = 2048  # per channel, in every ATRAC3plus frame
# The samples (at the track's rate) ffmpeg's decode of a track puts out before the movie's first: the encoder's
# lead-in, which a PSP clip's fact chunk declares as 2048 and a PMF does not declare, then the decoder's delay.
# Measured: MEV_201's Japanese track decodes 2632 samples at 48 kHz, 2418 at its 44.1 kHz, behind stock movie200
# (voices/dream_movies.py finds that offset every install).
START_PADDING = 2048 + ATRAC3PLUS_DECODER_DELAY
_RATES = (32_000, 44_100, 48_000, 88_200, 96_000)  # by bits 13-15 of the frame word (ffmpeg's ff_oma_srate_tab)
_LAYOUTS = {1: (1, 0x4), 2: (2, 0x3)}  # bits 10-12 -> (channels, WAVE channel mask): mono, stereo
_PACK, _SYSTEM, _END, _PRIVATE_1 = 0xBA, 0xBB, 0xB9, 0xBD


class PmfError(ValueError):
    """Not a PMF this reader understands; the message says where."""


def _program_stream(pmf: bytes) -> memoryview:
    if len(pmf) < 16 or pmf[:4] != b"PSMF":
        raise PmfError("no PSMF header")
    offset, size = struct.unpack_from(">II", pmf, 8)
    if offset != HEADER:
        raise PmfError(f"the PSMF header says the stream starts at {offset:#x}, not {HEADER:#x}")
    if offset + size > len(pmf):
        raise PmfError(f"the PSMF header says the stream is {size} bytes; the file holds {len(pmf) - offset}")
    return memoryview(pmf)[offset:offset + size]


def substreams(pmf: bytes) -> dict[int, bytes]:
    """Each private-1 substream's bytes, joined in stream order, by substream number."""
    stream = _program_stream(pmf)
    joined: dict[int, bytearray] = {}
    at = 0
    while at < len(stream):
        where = HEADER + at
        if len(stream) - at < 4 or stream[at:at + 3] != b"\0\0\1":
            raise PmfError(f"no start code at {where:#x}")
        code = stream[at + 3]
        if code == _END:
            break
        if code == _PACK:
            if at + 14 > len(stream) or stream[at + 4] >> 6 != 0b01:
                raise PmfError(f"the pack header at {where:#x} is not an MPEG-2 one")
            at += 14 + (stream[at + 13] & 7)
            continue
        if code < _SYSTEM or at + 6 > len(stream):
            raise PmfError(f"start code {code:#04x} at {where:#x} is not a packet")
        end = at + 6 + struct.unpack_from(">H", stream, at + 4)[0]
        if end > len(stream):
            raise PmfError(f"the packet at {where:#x} runs past the end of the stream")
        if code == _PRIVATE_1:
            if end < at + 9 or stream[at + 6] >> 6 != 0b10:
                raise PmfError(f"the private stream packet at {where:#x} has no MPEG-2 PES header")
            payload = at + 9 + stream[at + 8]
            if payload + 4 > end:
                raise PmfError(f"the private stream packet at {where:#x} has no substream header")
            joined.setdefault(stream[payload], bytearray()).extend(stream[payload + 4:end])
        at = end  # the system header, padding, video and private stream 2 are skipped whole
    return {number: bytes(data) for number, data in sorted(joined.items())}


def _frames(data: bytes, number: int) -> tuple[int, bytes]:
    """(the frame word, the frames without their headers) of one substream; every frame must share the word."""
    if len(data) < FRAME_HEADER:
        raise PmfError(f"substream {number} holds no frame")
    word = struct.unpack_from(">H", data, 2)[0]
    size = (word & 0x3FF) * 8 + 8
    step = FRAME_HEADER + size
    if len(data) % step:
        raise PmfError(f"substream {number} ends inside a frame ({len(data)} bytes, frames of {step})")
    frames = bytearray()
    for at in range(0, len(data), step):
        if data[at:at + 2] != b"\x0f\xd0" or struct.unpack_from(">H", data, at + 2)[0] != word:
            raise PmfError(f"substream {number}: frame header {data[at:at + 4].hex()} at byte {at}, "
                           f"not 0fd0{word:04x}")
        frames += data[at + FRAME_HEADER:at + step]
    return word, bytes(frames)


def _riff(word: int, frames: bytes, number: int) -> bytes:
    rate_bits, layout = word >> 13, (word >> 10) & 7
    if rate_bits >= len(_RATES) or layout not in _LAYOUTS:
        raise PmfError(f"substream {number}: frame word {word:#06x} names no supported rate and channel layout")
    rate, (channels, mask), block = _RATES[rate_bits], _LAYOUTS[layout], (word & 0x3FF) * 8 + 8
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, block * rate // FRAME_SAMPLES, block, 0, 22,
                      FRAME_SAMPLES, mask) + ATRAC3PLUS_GUID
    body = (b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(frames)) + frames
            + bytes(len(frames) & 1))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def audio_tracks(pmf: bytes) -> dict[int, bytes]:
    """Each audio substream of a PMF as an ATRAC3plus RIFF, by substream number. ffmpeg's decode of one starts
    with its decoder's delay and the encoder's lead-in: align it by its content, not by sample 0."""
    return {number: _riff(*_frames(data, number), number) for number, data in substreams(pmf).items()}
