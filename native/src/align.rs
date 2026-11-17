//! Lining two recordings up: where one sits inside the other, and at what gain.
//!
//! Both take mono 32-bit float samples as little-endian bytes (ffmpeg's `-f f32le -ac 1`). The search runs on an
//! FFT; the lag it finds is then checked against its neighbours by direct sums in f64, so the answer does not hang
//! on the FFT's rounding (which differs between the SIMD paths rustfft picks at run time).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rustfft::FftPlanner;
use rustfft::num_complex::Complex;

/// Lags either side of the FFT's best one that the direct sums compare.
const REFINE: usize = 8;
/// A stretch of the signal quieter than this fraction of the reference's energy (-60 dB) counts as silence: its
/// correlation is all rounding, so it cannot be the match.
const SILENCE: f64 = 1e-6;

fn samples(data: &[u8], what: &str) -> PyResult<Vec<f32>> {
    if data.len() % 4 != 0 {
        return Err(PyValueError::new_err(format!(
            "{what}: {} bytes is not a whole number of 32-bit samples",
            data.len()
        )));
    }
    let found: Vec<f32> = data
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect();
    if let Some(at) = found.iter().position(|v| !v.is_finite()) {
        return Err(PyValueError::new_err(format!("{what}: sample {at} is not a number")));
    }
    Ok(found)
}

fn dot(a: &[f32], b: &[f32]) -> f64 {
    a.iter().zip(b).map(|(&x, &y)| f64::from(x) * f64::from(y)).sum()
}

/// (normalised correlation, gain) of the signal stretch against the reference, from direct sums.
fn score(reference: &[f32], energy: f64, stretch: &[f32]) -> (f64, f64) {
    let own = dot(stretch, stretch);
    if own <= SILENCE * energy {
        return (0.0, 0.0);
    }
    let product = dot(reference, stretch);
    (product / (energy * own).sqrt(), product / energy)
}

/// The correlation of the reference with the signal at every lag that keeps it whole inside the signal.
fn correlations(reference: &[f32], signal: &[f32]) -> Vec<f32> {
    let size = signal.len().next_power_of_two();
    let mut planner = FftPlanner::<f32>::new();
    let (forward, inverse) = (planner.plan_fft_forward(size), planner.plan_fft_inverse(size));
    let padded = |values: &[f32]| {
        let mut out = vec![Complex::new(0.0f32, 0.0); size];
        for (slot, &v) in out.iter_mut().zip(values) {
            slot.re = v;
        }
        out
    };
    let (mut a, mut b) = (padded(reference), padded(signal));
    forward.process(&mut a);
    forward.process(&mut b);
    for (x, y) in b.iter_mut().zip(&a) {
        *x *= y.conj();
    }
    inverse.process(&mut b);
    // No lag wraps: a lag past signal.len() - reference.len() is never read, and size >= signal.len().
    b[..=signal.len() - reference.len()].iter().map(|c| c.re / size as f32).collect()
}

/// The normalised correlation of the reference with the signal at every lag that keeps it whole inside the signal
/// (FFT precision), and NEG_INFINITY at a lag where the signal is silence.
fn normalised(reference: &[f32], signal: &[f32]) -> Vec<f64> {
    let energy = dot(reference, reference);
    let (n, last) = (reference.len(), signal.len() - reference.len());
    let mut running: f64 = signal[..n].iter().map(|&v| f64::from(v) * f64::from(v)).sum();
    let correlation = correlations(reference, signal);
    (0..=last)
        .map(|lag| {
            if lag > 0 {
                let (out, inn) = (f64::from(signal[lag - 1]), f64::from(signal[lag + n - 1]));
                running += inn * inn - out * out;
            }
            let window = running.max(0.0);
            if window <= SILENCE * energy {
                f64::NEG_INFINITY
            } else {
                f64::from(correlation[lag]) / (energy * window).sqrt()
            }
        })
        .collect()
}

fn best_lag(reference: &[f32], signal: &[f32]) -> (usize, f64, f64) {
    let energy = dot(reference, reference);
    let (n, last) = (reference.len(), signal.len() - reference.len());
    let mut rough = (0usize, f64::NEG_INFINITY);
    for (lag, c) in normalised(reference, signal).into_iter().enumerate() {
        if c > rough.1 {
            rough = (lag, c);
        }
    }
    let mut best = (rough.0, f64::NEG_INFINITY, 0.0);
    for lag in rough.0.saturating_sub(REFINE)..=(rough.0 + REFINE).min(last) {
        let (normalised, gain) = score(reference, energy, &signal[lag..lag + n]);
        if normalised > best.1 {
            best = (lag, normalised, gain);
        }
    }
    best
}

/// (lag, peak, gain): where the reference best matches the signal, `signal[lag..lag + len(reference)]`, searched
/// over every lag that keeps the reference whole inside the signal (0 to len(signal) - len(reference)).
///
/// peak is the normalised correlation there, <r, s> / sqrt(<r, r> <s, s>) over that stretch (1 for a scaled copy,
/// near 0 for unrelated sound); gain is the least-squares g of stretch = g * reference, <r, s> / <r, r>. The
/// earliest lag wins a tie. A stretch more than 60 dB below the reference's energy cannot match.
#[pyfunction]
pub fn xcorr_lag(py: Python<'_>, reference: &[u8], signal: &[u8]) -> PyResult<(usize, f64, f64)> {
    let (reference, signal) = (samples(reference, "reference")?, samples(signal, "signal")?);
    if reference.is_empty() || reference.len() > signal.len() {
        return Err(PyValueError::new_err(format!(
            "the reference ({} samples) must be shorter than the signal ({} samples) and not empty",
            reference.len(),
            signal.len()
        )));
    }
    if dot(&reference, &reference) == 0.0 {
        return Err(PyValueError::new_err("the reference is silent"));
    }
    Ok(py.detach(|| best_lag(&reference, &signal)))
}

/// The least-squares gain g of signal = g * reference for two aligned recordings of one length: <r, s> / <r, r>.
#[pyfunction]
pub fn fit_gain(reference: &[u8], signal: &[u8]) -> PyResult<f64> {
    let (reference, signal) = (samples(reference, "reference")?, samples(signal, "signal")?);
    if reference.len() != signal.len() {
        return Err(PyValueError::new_err(format!(
            "the reference has {} samples and the signal {}",
            reference.len(),
            signal.len()
        )));
    }
    let energy = dot(&reference, &reference);
    if energy == 0.0 {
        return Err(PyValueError::new_err("the reference is silent"));
    }
    Ok(dot(&reference, &signal) / energy)
}

/// The drops of a sped-up copy of the reference inside the signal, block by block.
///
/// Block k is reference[k * hop .. k * hop + block] (silence past its end), for k = 0 .. scored.len(). A copy that
/// has dropped d samples before block k holds it at signal[max_drop + k * hop - d ..]: the signal starts max_drop
/// samples before the latest place the reference may start. Block 0's drop is any of 0 to free (where the copy
/// starts); from one block to the next the drop grows by 0 to hop (the copy plays at most twice as fast), up to
/// max_drop. The path chosen maximises the sum of the scored blocks' normalised correlations there. Returns (drop,
/// correlation) per block, correlation 0 for an unscored one.
#[pyfunction]
pub fn splice_drops(
    py: Python<'_>,
    reference: &[u8],
    signal: &[u8],
    block: usize,
    hop: usize,
    max_drop: usize,
    free: usize,
    scored: Vec<bool>,
) -> PyResult<Vec<(usize, f64)>> {
    let (reference, signal) = (samples(reference, "reference")?, samples(signal, "signal")?);
    let blocks = scored.len();
    if block == 0 || hop == 0 || hop > block || hop > usize::from(u16::MAX) || blocks == 0 || free > max_drop {
        return Err(PyValueError::new_err("splice_drops needs blocks, a block length and a hop of 1 to the block"));
    }
    if signal.len() < max_drop + (blocks - 1) * hop + block {
        return Err(PyValueError::new_err(format!(
            "the signal ({} samples) ends before the last block's place ({})",
            signal.len(),
            max_drop + (blocks - 1) * hop + block
        )));
    }
    Ok(py.detach(|| {
        let states = max_drop + 1;
        let mut best = vec![f64::NEG_INFINITY; states];
        let mut steps: Vec<Vec<u16>> = Vec::with_capacity(blocks); // per block and drop: the drop gained into it
        let mut scores: Vec<Vec<f64>> = Vec::with_capacity(blocks);
        for (k, &scoring) in scored.iter().enumerate() {
            let reach = (free + k * hop).min(max_drop); // the largest drop block k can have
            let score = if scoring {
                let mut piece = vec![0f32; block];
                let start = (k * hop).min(reference.len());
                let end = (k * hop + block).min(reference.len());
                piece[..end - start].copy_from_slice(&reference[start..end]);
                if dot(&piece, &piece) == 0.0 {
                    vec![0.0; reach + 1]
                } else {
                    let first = max_drop + k * hop - reach; // the signal index at drop `reach`
                    let found = normalised(&piece, &signal[first..first + reach + block]);
                    // found[i] is drop reach - i
                    (0..=reach).map(|d| found[reach - d].max(-1.0)).collect()
                }
            } else {
                vec![0.0; reach + 1]
            };
            let mut next = vec![f64::NEG_INFINITY; states];
            let mut step = vec![0u16; reach + 1];
            if k == 0 {
                next[..=reach].copy_from_slice(&score); // block 0 starts the copy: any drop up to free
            }
            // Later blocks: new[d] = score[d] + max(best[d - hop ..= d]), by a monotone deque over d.
            let mut window: std::collections::VecDeque<usize> = std::collections::VecDeque::new();
            for d in (0..=reach).filter(|_| k > 0) {
                while window.back().is_some_and(|&e| best[e] <= best[d]) {
                    window.pop_back();
                }
                window.push_back(d);
                while window.front().is_some_and(|&e| e + hop < d) {
                    window.pop_front();
                }
                let from = *window.front().unwrap();
                if best[from] > f64::NEG_INFINITY {
                    next[d] = best[from] + score[d];
                    step[d] = (d - from) as u16;
                }
            }
            best = next;
            steps.push(step);
            scores.push(score);
        }
        let mut d = (0..states).fold(0, |a, b| if best[b] > best[a] { b } else { a });
        let mut path = vec![(0usize, 0f64); blocks];
        for k in (0..blocks).rev() {
            path[k] = (d, if scored[k] { scores[k][d] } else { 0.0 });
            if k > 0 {
                d -= usize::from(steps[k][d]);
            }
        }
        path
    }))
}
