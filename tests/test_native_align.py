"""The native alignment search (native/src/align.rs) on synthetic noise. Skipped without `wozmod-native`."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
native = pytest.importorskip("wozmod_native")


def _f32(values) -> bytes:
    return np.asarray(values, dtype="<f4").tobytes()


def test_a_planted_clip_is_found_at_its_lag_with_its_gain():
    # Bug: the FFT product is conjugated on the wrong side (the search runs backwards in time), the last lag that
    # keeps the clip whole is left out, a stretch of digital silence wins on rounding (its energy is zero), or the
    # gain is divided by the wrong energy.
    rng = np.random.default_rng(7)
    clip = rng.standard_normal(3000)
    rows = []
    for lag, gain, floor in ((0, 0.7, 0.05), (1, 0.7, 0.05), (12_345, 0.3, 0.05), (47_000, 0.7, 0.05),
                             (20_000, 0.02, 0.0)):  # the last: a quiet clip in silence
        signal = floor * rng.standard_normal(50_000)
        signal[lag:lag + len(clip)] += gain * clip
        found, peak, fitted = native.xcorr_lag(_f32(clip), _f32(signal))
        expected = (lag, gain / np.sqrt(gain ** 2 + floor ** 2))  # the clip's share of the stretch's energy
        if found != lag or abs(peak - expected[1]) > 0.02 or abs(fitted - gain) > 0.01:
            rows.append((lag, gain, floor, found, round(peak, 3), round(fitted, 3)))
    assert rows == []


def test_the_gain_fit_is_the_least_squares_scale():
    # Bug: the gain is the ratio of the energies (its square root), which also counts the part of the signal the
    # reference does not explain.
    rng = np.random.default_rng(8)
    reference, other = rng.standard_normal(10_000), rng.standard_normal(10_000)
    other -= reference * (other @ reference) / (reference @ reference)  # orthogonal to the reference
    assert native.fit_gain(_f32(reference), _f32(0.4 * reference + other)) == pytest.approx(0.4, abs=1e-4)


def test_a_spliced_copy_starts_no_earlier_than_free_allows():
    # Bug: block 0 already takes a hop of drop from the state before it, so a copy may start one hop (10 ms in the
    # Dream scenes) before the earliest place a stock clip may start, or the copy at the edge of that range is missed.
    rng = np.random.default_rng(403)
    block, hop, blocks, most, free = 512, 128, 6, 2000, 256
    reference = rng.standard_normal(block + (blocks - 1) * hop)
    rows = []
    for planted in (0, free // 2, free, free + hop):  # the drop the copy has throughout; the last starts too early
        signal = 0.05 * rng.standard_normal(most + (blocks - 1) * hop + block)
        signal[most - planted:most - planted + len(reference)] += reference
        drops = [d for d, _ in native.splice_drops(_f32(reference), _f32(signal), block, hop, most, free,
                                                   [True] * blocks)]
        expected = drops[0] <= free and drops[1:] == [planted] * (blocks - 1) if planted > free else \
            drops == [planted] * blocks
        if not expected:
            rows.append((planted, drops))
    assert rows == []
