//! The per-sample loops of cutting the Z Dream scenes (wozmod.voices.dream_events), off Python's interpreter.
//!
//! Each function gives exactly what the Python it replaced gave, bit for bit, so the takes do not change: samples are
//! 32-bit floats widened to double for each operation and rounded back to 32 bits where Python stored them in an
//! `array("f")`. tests/test_dream_events.py compares each one with the Python it replaced. The loops let go of the GIL, so the scenes can be cut on threads.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn samples(data: &[u8], what: &str) -> PyResult<Vec<f32>> {
    if data.len() % 4 != 0 {
        return Err(PyValueError::new_err(format!(
            "{what}: {} bytes is not a whole number of 32-bit samples",
            data.len()
        )));
    }
    Ok(data
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect())
}

fn to_bytes<'py>(py: Python<'py>, values: &[f32]) -> Bound<'py, PyBytes> {
    PyBytes::new_with(py, values.len() * 4, |out| {
        for (chunk, v) in out.chunks_exact_mut(4).zip(values) {
            chunk.copy_from_slice(&v.to_le_bytes());
        }
        Ok(())
    })
    .expect("writing into a fresh bytes object cannot fail")
}

/// samples[start .. start + length], silence where that runs past either end (dream_events._stretch).
fn stretch(samples: &[f32], start: i64, length: usize) -> Vec<f32> {
    (0..length as i64)
        .map(|i| {
            let at = start + i;
            if at >= 0 && (at as usize) < samples.len() {
                samples[at as usize]
            } else {
                0.0
            }
        })
        .collect()
}

/// max(map(abs, frame)) over 32-bit samples, as a double; 0 for no samples.
fn peak(frame: &[f32]) -> f64 {
    frame
        .iter()
        .fold(0.0f64, |most, &v| most.max(f64::from(v.abs())))
}

/// The peak (max of abs) of each frame of `size` samples every `step`: as many frames as fit whole, or with `count`,
/// that many frames, silence past the end (dream_events._peaks).
#[pyfunction]
#[pyo3(signature = (data, size, step, count=None))]
pub fn frame_peaks(
    py: Python<'_>,
    data: &[u8],
    size: usize,
    step: usize,
    count: Option<usize>,
) -> PyResult<Vec<f64>> {
    let values = samples(data, "samples")?;
    if size == 0 || step == 0 {
        return Err(PyValueError::new_err(
            "frame_peaks needs a frame size and a step of at least one sample",
        ));
    }
    Ok(py.detach(|| match count {
        None if values.len() < size => Vec::new(),
        None => (0..=values.len() - size)
            .step_by(step)
            .map(|i| peak(&values[i..i + size]))
            .collect(),
        Some(n) => (0..n)
            .map(|k| peak(&stretch(&values, (k * step) as i64, size)))
            .collect(),
    }))
}

/// a - b, sample by sample, each difference taken in double and stored as a 32-bit sample.
#[pyfunction]
pub fn difference<'py>(py: Python<'py>, a: &[u8], b: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let (a, b) = (samples(a, "a")?, samples(b, "b")?);
    if a.len() != b.len() {
        return Err(PyValueError::new_err(format!(
            "a has {} samples and b {}",
            a.len(),
            b.len()
        )));
    }
    let out: Vec<f32> = py.detach(|| {
        a.iter()
            .zip(&b)
            .map(|(&x, &y)| (f64::from(x) - f64::from(y)) as f32)
            .collect()
    });
    Ok(to_bytes(py, &out))
}

/// The samples times factor, each product in double stored as a 32-bit sample.
#[pyfunction]
pub fn scaled<'py>(py: Python<'py>, data: &[u8], factor: f64) -> PyResult<Bound<'py, PyBytes>> {
    let values = samples(data, "samples")?;
    let out: Vec<f32> = py.detach(|| {
        values
            .iter()
            .map(|&v| (factor * f64::from(v)) as f32)
            .collect()
    });
    Ok(to_bytes(py, &out))
}

/// The blocks of `block` samples every `hop` of data (silence past its end), each weighted by window and added at
/// its place, divided by the summed weights (0 where none): dream_events._spliced's copy, with the total and the
/// weights in double, added block after block.
#[pyfunction]
pub fn overlap_add<'py>(
    py: Python<'py>,
    data: &[u8],
    places: Vec<i64>,
    window: Vec<f64>,
    hop: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let values = samples(data, "samples")?;
    let block = window.len();
    if places.is_empty() || block == 0 {
        return Err(PyValueError::new_err(
            "overlap_add needs at least one block and a window",
        ));
    }
    let out = py.detach(|| {
        let first = *places.iter().min().expect("not empty");
        let length = (*places.iter().max().expect("not empty") - first) as usize + block;
        let (mut total, mut weight) = (vec![0.0f64; length], vec![0.0f64; length]);
        for (k, &place) in places.iter().enumerate() {
            let piece = stretch(&values, (k * hop) as i64, block);
            let offset = (place - first) as usize;
            for (i, (&w, &v)) in window.iter().zip(&piece).enumerate() {
                total[offset + i] += w * f64::from(v);
                weight[offset + i] += w;
            }
        }
        total
            .iter()
            .zip(&weight)
            .map(|(&t, &w)| if w != 0.0 { (t / w) as f32 } else { 0.0 })
            .collect::<Vec<f32>>()
    });
    Ok(to_bytes(py, &out))
}
