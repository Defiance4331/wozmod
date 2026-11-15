"""The master stage on the finished mix: +5.6 dB, then a lookahead true-peak limiter at -1 dBTP
(runtime/sound_master.h)."""

from __future__ import annotations

import ctypes
import struct

import numpy as np
import pytest


RATE = 48000
GAIN_DB, CEILING_DBTP = 5.6, -1.0  # the owner's decision: normal play at -16 LUFS, true peak at most -1 dBTP
SILENT = 2  # AUDCLNT_BUFFERFLAGS_SILENT


@pytest.fixture(scope="module")
def master(c_library):
    lib = c_library("master", r'''
#include "sound_master.h"
unsigned long size(void) { return sizeof(struct master); }
int init(struct master *m, const unsigned char *w)
{
    struct master_format f = master_format_of(w);
    return f.kind != MASTER_NONE && master_init(m, f.rate, f.channels);
}
unsigned int delay(const struct master *m) { return m->line_frames - 1; }
float gain(const struct master *m) { return m->gain; }
int kind(const unsigned char *w) { return master_format_of(w).kind; }
unsigned int release(struct master *m, const unsigned char *w, unsigned char *data, unsigned int frames,
                     unsigned int flags)
{
    struct master_format f = master_format_of(w);
    return master_release(m, &f, data, frames, flags);
}
''')
    lib.size.restype = ctypes.c_ulong
    lib.gain.restype = ctypes.c_float
    lib.init.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.delay.argtypes = lib.gain.argtypes = [ctypes.c_void_p]
    lib.kind.argtypes = [ctypes.c_char_p]
    lib.release.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    return lib


def wave_format(tag: int, channels: int, bits: int, rate: int = RATE) -> bytes:
    block = channels * bits // 8
    return struct.pack("<HHIIHHH", tag, channels, rate, rate * block, block, bits, 0)


def extensible(subtype: int, channels: int, bits: int, valid: int) -> bytes:
    block = channels * bits // 8
    guid = struct.pack("<IHH", subtype, 0, 0x10) + bytes([0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71])
    return struct.pack("<HHIIHHHHI", 0xFFFE, channels, RATE, RATE * block, block, bits, 22, valid, 3) + guid


class Stream:
    """One stream through the stage, as the runtime's ReleaseBuffer hook runs it: blocks of frames, in place."""

    def __init__(self, lib, form: bytes):
        self.lib, self.form = lib, form
        self.state = ctypes.create_string_buffer(lib.size())
        assert lib.init(self.state, form)

    def release(self, block: np.ndarray, flags: int = 0, frames: int | None = None) -> int:
        """One ReleaseBuffer of block (frames, channels), or of `frames` frames of raw bytes; the flags passed on."""
        frames = len(block) if frames is None else frames
        return self.lib.release(self.state, self.form, block.ctypes.data, frames, flags)

    def run(self, x: np.ndarray, block: int = 480) -> np.ndarray:
        out = np.ascontiguousarray(x, np.float32).copy()
        for at in range(0, len(out), block):
            assert self.release(out[at:at + block]) == 0
        return out

    @property
    def delay(self) -> int:
        return self.lib.delay(self.state)

    @property
    def gain(self) -> float:
        return self.lib.gain(self.state)


def true_peak_db(x: np.ndarray) -> float:
    """An independent true-peak meter: band-limited (FFT) interpolation to 4x, past zeros either side."""
    pad = np.zeros((1024, x.shape[1]))
    x = np.concatenate([pad, x, pad])
    n = len(x) + len(x) % 2
    spectrum = np.fft.rfft(x, n=n, axis=0)
    wide = np.zeros((2 * n + 1, x.shape[1]), complex)
    wide[:n // 2 + 1] = spectrum
    wide[n // 2] *= 0.5  # the Nyquist bin, split between its two images
    return float(20 * np.log10(np.abs(np.fft.irfft(wide, n=4 * n, axis=0) * 4).max()))


def faded(x: np.ndarray, tail: int = 4800) -> np.ndarray:
    """x with 10 ms raised-cosine ends and a tail of silence, so the meter sees no cut and the delay line empties."""
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(480) / 480)
    window = np.ones(len(x))
    window[:480], window[-480:] = ramp, ramp[::-1]
    return np.concatenate([x * window[:, None], np.zeros((tail, x.shape[1]))])


def band_limited_noise(seconds: float, top_hz: float, seed: int) -> np.ndarray:
    noise = np.random.default_rng(seed).standard_normal((int(RATE * seconds), 2))
    spectrum = np.fft.rfft(noise, axis=0)
    spectrum[np.fft.rfftfreq(len(noise), 1 / RATE) > top_hz] = 0
    return np.fft.irfft(spectrum, n=len(noise), axis=0)


def test_loud_signals_come_out_at_or_under_the_ceiling(master):
    """Bug: the limiter reads sample peaks only, so a peak between samples passes (the fs/4 sine at 45 degrees has
    every sample 3 dB under its true peak), or its gain reaches a frame later or earlier than the frame it was read
    on, or a channel is left out of the linked gain. Each output is measured by an independent 4x meter; each should
    also sit near the ceiling, not crushed under it (a wrong need, such as one squared, over-limits)."""
    gain = 10 ** (GAIN_DB / 20)
    t = np.arange(RATE) / RATE
    tone = 10 ** ((CEILING_DBTP + 6) / 20) / gain * np.sin(2 * np.pi * 997 * t)  # 6 dB over the ceiling
    between = 10 ** (2 / 20) / gain * np.sin(np.pi * np.arange(RATE) / 2 + np.pi / 4)  # +2 dBTP, samples at -1.01
    bursts = band_limited_noise(1, 20000, 1) * 0.3 / gain * (1 + 3 * (np.arange(RATE)[:, None] % 9000 < 300))
    cases = {
        "997 Hz, 6 dB over, right channel 6 dB under the left": np.stack([tone, tone / 2], 1),
        "fs/4 at 45 degrees, +2 dBTP, samples under the ceiling": np.stack([between, between], 1),
        "noise to 20 kHz, bursts 13 dB over": bursts,
        "silent left, loud right": np.stack([0 * tone, tone], 1),
    }
    peaks = {name: true_peak_db(Stream(master, wave_format(3, 2, 32)).run(faded(x))) for name, x in cases.items()}
    assert {name: peak for name, peak in peaks.items() if not CEILING_DBTP - 0.3 <= peak <= CEILING_DBTP} == {}


def test_a_signal_under_the_ceiling_comes_out_as_the_input_times_the_gain_delayed(master):
    """Bug: the gain is the wrong level, or the output is a frame early or late against the delay, or the limiter's
    gain sits a hair under 1 with nothing to limit (a running sum that drifts), so quiet play is not left exactly as
    the mix made it. Peaks at -9 dBTP after the gain."""
    stream = Stream(master, wave_format(3, 2, 32))
    assert abs(stream.gain / 10 ** (GAIN_DB / 20) - 1) < 1e-6
    x = (band_limited_noise(1, 16000, 2) * 0.05).astype(np.float32)
    y = stream.run(x, block=441)
    assert true_peak_db(x * stream.gain) < -8
    assert stream.delay * 1000 / RATE < 3  # the lookahead and the detector's span, about 2.6 ms
    assert np.array_equal(y[stream.delay:], x[:-stream.delay] * np.float32(stream.gain))
    assert not y[:stream.delay].any()


def test_the_gain_returns_to_exactly_one_after_a_peak(master):
    """Bug: the release creeps towards 1 and stops short (a float envelope stalls once its step rounds away), so after
    the first loud moment the whole session stays a little quieter than input times gain."""
    stream = Stream(master, wave_format(3, 2, 32))
    gain = 10 ** (GAIN_DB / 20)
    loud = np.full((480, 2), 2 / gain, np.float32)  # +6 dB over full scale: about 7 dB of reduction
    quiet = (band_limited_noise(4, 16000, 3) * 0.05).astype(np.float32)
    y = stream.run(np.concatenate([loud, quiet]))
    after = slice(480 + stream.delay + 3 * RATE, None)  # 3 s on: 25 release time constants
    assert np.array_equal(y[after], quiet[3 * RATE:-stream.delay] * np.float32(stream.gain))


def test_a_silent_block_still_plays_the_delay_lines_tail(master):
    """Bug: a block flagged AUDCLNT_BUFFERFLAGS_SILENT is passed on with the flag (the end of the sound before it,
    still in the delay line, is never heard) or is run through the stage as the garbage its buffer holds."""
    stream = Stream(master, wave_format(3, 2, 32))
    sound = np.full((480, 2), 0.25, np.float32)
    stream.run(sound)
    silent = np.full((480, 2), 123.0, np.float32)  # what a silent block's buffer may hold: anything
    assert stream.release(silent, SILENT) == 0
    tail = np.zeros((480, 2), np.float32)
    tail[:stream.delay] = np.float32(0.25) * np.float32(stream.gain)
    assert np.array_equal(silent, tail)


def test_each_sample_format_runs_as_the_same_signal(master):
    """Bug: a format is misread, so the stage runs on noise: a WAVEFORMATEXTENSIBLE float stream taken for integers,
    24-bit samples sign-extended wrong, 24 valid bits in a 32-bit container taken from the low bits, a byte order
    swapped. Every format the stage takes gives the float stream's output to within its own rounding; every other
    is not taken, so its stream passes through untouched."""
    gain = 10 ** (GAIN_DB / 20)
    x = np.sin(2 * np.pi * 440 * np.arange(4800) / RATE)[:, None] * np.array([0.9, -0.5]) / gain  # one peak over
    reference = Stream(master, wave_format(3, 2, 32)).run(x.astype(np.float32))
    encodings = {  # format: (bytes, dtype, full scale, error allowed in full-scale units)
        "float32": (wave_format(3, 2, 32), "<f4", 1, 0),  # the same floats: exactly
        "float32 extensible": (extensible(3, 2, 32, 32), "<f4", 1, 0),
        # integers: the input's rounding, times the gain, and the output's
        "int16": (wave_format(1, 2, 16), "<i2", 2 ** 15, 2.5 / 2 ** 15),
        "int24": (wave_format(1, 2, 24), None, 2 ** 23, 2.5 / 2 ** 23),
        "int32": (wave_format(1, 2, 32), "<i4", 2 ** 31, 2.5 / 2 ** 23),  # the stage runs in 24-bit floats
        "int24-in-32 extensible": (extensible(1, 2, 32, 24), "<i4", 2 ** 31, 2.5 / 2 ** 23),
    }
    worst = {}
    for name, (form, dtype, scale, allowed) in encodings.items():
        ints = np.round(x * scale).astype(np.int64)
        if dtype is None:
            data = np.frombuffer(b"".join(int(v).to_bytes(3, "little", signed=True) for v in ints.ravel()), np.uint8)
        else:
            data = (x if dtype == "<f4" else ints).astype(dtype).view(np.uint8)
        data = data.copy()
        stream = Stream(master, form)
        assert stream.release(data, 0, frames=len(x)) == 0
        if dtype is None:
            out = np.array([int.from_bytes(bytes(data[i:i + 3]), "little", signed=True)
                            for i in range(0, len(data), 3)]).reshape(-1, 2) / scale
        else:
            out = data.view(dtype).reshape(-1, 2) / scale
        error = float(np.abs(out - reference).max())
        if error > allowed:
            worst[name] = error
    assert worst == {}
    unsupported = {"8-bit": wave_format(1, 2, 8), "float64": wave_format(3, 2, 64), "mu-law": wave_format(7, 2, 8),
                   "extensible, a foreign SubFormat": extensible(1, 2, 16, 16)[:-1] + b"\x00",
                   "33 channels": wave_format(3, 33, 32)}
    assert {name: master.kind(form) for name, form in unsupported.items() if master.kind(form)} == {}
