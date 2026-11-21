//! The Dream voice separator (wozmod.voices.separation): the STFT of a chunk of stereo sound, the voice a set of
//! masks leaves of it, and the network that makes the masks, run by ONNX Runtime.
//!
//! The network is the seam: spectra in, masks out (here on the CPU; cuda.rs on an NVIDIA GPU). Everything else here
//! is plain signal code that does not know which runtime or device made the masks.
//!
//! The STFT is the model's (torch.stft as Mel-Band RoFormer calls it): 2048 samples a frame, a frame every 441, a
//! periodic Hann window, the sound mirrored by 1024 samples at both ends. A spectrum is frames x 2050 x 2 as the
//! network takes it, 32-bit floats: row 2 * bin + channel, then the frame, then (real, imaginary).

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rustfft::{num_complex::Complex, FftPlanner};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use ort::environment::Environment;
use ort::session::Session;
use ort::value::TensorRef;

const FRAME: usize = 2048;
const HOP: usize = 441;
const BINS: usize = FRAME / 2 + 1;
pub const ROWS: usize = 2 * BINS;

pub fn floats(data: &[u8]) -> Vec<f32> {
    data.chunks_exact(4).map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]])).collect()
}

pub fn bytes(values: &[f32]) -> Vec<u8> {
    values.iter().flat_map(|v| v.to_le_bytes()).collect()
}

fn window() -> Vec<f64> {
    (0..FRAME).map(|i| 0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / FRAME as f64).cos()).collect()
}

/// The frames of `samples` samples a channel.
fn frames_of(samples: usize) -> usize {
    1 + samples / HOP
}

/// The spectrum of interleaved stereo sound.
fn spectrum(sound: &[f32]) -> Vec<f32> {
    let samples = sound.len() / 2;
    let frames = frames_of(samples);
    let window = window();
    let forward = FftPlanner::<f64>::new().plan_fft_forward(FRAME);
    let mut out = vec![0f32; ROWS * frames * 2];
    let mut buffer = vec![Complex::new(0.0, 0.0); FRAME];
    let at = |i: usize| -> usize {
        // the sample a padded position holds: the sound mirrored about its first and last samples
        let i = i as isize - (FRAME / 2) as isize;
        let last = samples as isize - 1;
        (if i < 0 { -i } else if i > last { 2 * last - i } else { i }) as usize
    };
    for channel in 0..2 {
        for frame in 0..frames {
            for (k, cell) in buffer.iter_mut().enumerate() {
                *cell = Complex::new(sound[2 * at(frame * HOP + k) + channel] as f64 * window[k], 0.0);
            }
            forward.process(&mut buffer);
            for bin in 0..BINS {
                let cell = (((2 * bin + channel) * frames) + frame) * 2;
                out[cell] = buffer[bin].re as f32;
                out[cell + 1] = buffer[bin].im as f32;
            }
        }
    }
    out
}

/// The sound (`samples` a channel, interleaved stereo) of a spectrum under masks. `masks` is frames x width, a
/// (real, imaginary) pair per band bin and channel in band order; a bin that several bands hold gets the mean of
/// their masks. `bands` is each band's (first bin, bins).
fn masked(spec: &[f32], masks: &[f32], bands: &[(usize, usize)], samples: usize) -> Result<Vec<f32>, String> {
    let frames = frames_of(samples);
    let width: usize = bands.iter().map(|&(_, bins)| 4 * bins).sum();
    if spec.len() != ROWS * frames * 2 || masks.len() != frames * width {
        return Err(format!("a spectrum of {} values and masks of {} do not fit {frames} frames", spec.len(), masks.len()));
    }
    let mut held = vec![0f64; BINS];
    for &(first, bins) in bands {
        if first + bins > BINS {
            return Err("a band runs past the last bin".into());
        }
        (first..first + bins).for_each(|bin| held[bin] += 1.0);
    }
    let window = window();
    let inverse = FftPlanner::<f64>::new().plan_fft_inverse(FRAME);
    let length = FRAME + HOP * (frames - 1);
    let mut weight = vec![0f64; length];
    for frame in 0..frames {
        for k in 0..FRAME {
            weight[frame * HOP + k] += window[k] * window[k];
        }
    }
    let mut out = vec![0f32; 2 * samples];
    let mut mask = vec![Complex::new(0.0, 0.0); BINS];
    let mut buffer = vec![Complex::new(0.0, 0.0); FRAME];
    for channel in 0..2 {
        let mut sound = vec![0f64; length];
        for frame in 0..frames {
            mask.fill(Complex::new(0.0, 0.0));
            let mut column = 0;
            for &(first, bins) in bands {
                for bin in first..first + bins {
                    let cell = frame * width + 2 * (column + 2 * (bin - first) + channel);
                    mask[bin] += Complex::new(masks[cell] as f64, masks[cell + 1] as f64);
                }
                column += 2 * bins;
            }
            for bin in 0..BINS {
                let cell = (((2 * bin + channel) * frames) + frame) * 2;
                let value = Complex::new(spec[cell] as f64, spec[cell + 1] as f64) * mask[bin] / held[bin].max(1e-8);
                buffer[bin] = value;
                if bin > 0 && bin < FRAME / 2 {
                    buffer[FRAME - bin] = value.conj();
                }
            }
            buffer[0].im = 0.0; // a real signal's spectrum: what irfft takes of these two bins
            buffer[FRAME / 2].im = 0.0;
            inverse.process(&mut buffer);
            for k in 0..FRAME {
                sound[frame * HOP + k] += buffer[k].re / FRAME as f64 * window[k];
            }
        }
        for i in 0..samples {
            let k = i + FRAME / 2;
            out[2 * i + channel] = (if weight[k] > 1e-11 { sound[k] / weight[k] } else { sound[k] }) as f32;
        }
    }
    Ok(out)
}

fn sound_of(data: &[u8]) -> PyResult<Vec<f32>> {
    if data.len() % 8 != 0 || data.len() / 8 <= FRAME / 2 {
        return Err(PyValueError::new_err("the sound is not interleaved stereo 32-bit floats of over 1024 samples"));
    }
    Ok(floats(data))
}

/// separator_spectrum(sound) -> the spectrum of interleaved stereo 32-bit float sound, as the network takes it.
#[pyfunction]
pub fn separator_spectrum<'py>(py: Python<'py>, sound: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let sound = sound_of(sound)?;
    Ok(PyBytes::new(py, &py.detach(|| bytes(&spectrum(&sound)))))
}

/// separator_voice(spec, masks, bands, samples) -> the sound the masks leave of a spectrum: interleaved stereo
/// 32-bit floats, samples a channel.
#[pyfunction]
pub fn separator_voice<'py>(py: Python<'py>, spec: &[u8], masks: &[u8], bands: Vec<(usize, usize)>, samples: usize) -> PyResult<Bound<'py, PyBytes>> {
    if samples <= FRAME / 2 {
        return Err(PyValueError::new_err("the sound is not over 1024 samples"));
    }
    let (spec, masks) = (floats(spec), floats(masks));
    let out = py.detach(|| masked(&spec, &masks, &bands, samples)).map_err(PyValueError::new_err)?;
    Ok(PyBytes::new(py, &bytes(&out)))
}

/// separator_joined(length, chunks) -> `length` samples a channel of interleaved stereo: each chunk (first sample,
/// sound) laid at its place. Where chunks overlap, the sound fades from the earlier to the later across the overlap
/// (the weighted mean of the chunks that hold a sample); a sample no chunk holds is silence.
#[pyfunction]
pub fn separator_joined<'py>(py: Python<'py>, length: usize, chunks: Vec<(usize, Vec<u8>)>) -> PyResult<Bound<'py, PyBytes>> {
    let chunks: Vec<(usize, Vec<f32>)> = chunks.into_iter().map(|(at, sound)| (at, floats(&sound))).collect();
    if chunks.windows(2).any(|pair| pair[0].0 >= pair[1].0) || chunks.iter().any(|(at, sound)| at + sound.len() / 2 > length) {
        return Err(PyValueError::new_err("the chunks are not in order inside the sound"));
    }
    let out = py.detach(|| {
        let mut out = vec![0f64; 2 * length];
        let mut total = vec![0f64; length];
        for (k, (at, sound)) in chunks.iter().enumerate() {
            let samples = sound.len() / 2;
            // the samples at each end that a neighbour also holds
            let before = if k > 0 { (chunks[k - 1].0 + chunks[k - 1].1.len() / 2).saturating_sub(*at).min(samples) } else { 0 };
            let after = chunks.get(k + 1).map_or(0, |next| (at + samples).saturating_sub(next.0).min(samples));
            for i in 0..samples {
                let mut weight = 1f64;
                if i < before {
                    weight *= (i + 1) as f64 / (before + 1) as f64;
                }
                if i >= samples - after {
                    weight *= (samples - i) as f64 / (after + 1) as f64;
                }
                total[at + i] += weight;
                for channel in 0..2 {
                    out[2 * (at + i) + channel] += sound[2 * i + channel] as f64 * weight;
                }
            }
        }
        (0..2 * length).map(|i| if total[i / 2] > 0.0 { (out[i] / total[i / 2]) as f32 } else { 0.0 }).collect::<Vec<f32>>()
    });
    Ok(PyBytes::new(py, &bytes(&out)))
}

/// separator_agreement(masks, reference) -> the energy of masks - reference against reference's, in dB: how far
/// one network's masks are from another's. +inf when masks holds a value that is not finite or the two differ in
/// length.
#[pyfunction]
pub fn separator_agreement(py: Python<'_>, masks: &[u8], reference: &[u8]) -> f64 {
    if masks.len() != reference.len() {
        return f64::INFINITY;
    }
    py.detach(|| {
        let (mut error, mut energy) = (0f64, 0f64);
        for (a, b) in floats(masks).iter().zip(floats(reference).iter()) {
            if !a.is_finite() {
                return f64::INFINITY;
            }
            error += (*a as f64 - *b as f64).powi(2);
            energy += (*b as f64).powi(2);
        }
        if error == 0.0 { f64::NEG_INFINITY } else { 10.0 * (error / energy).log10() }
    })
}

/// separator_cores() -> the machine's physical cores: the threads ONNX Runtime's CPU kernels gain from.
#[pyfunction]
pub fn separator_cores() -> usize {
    num_cpus::get_physical()
}

static RUNTIME: OnceLock<Result<Environment, String>> = OnceLock::new();

/// Load a library so that Windows looks for the libraries it imports beside it first. Left to its standard order,
/// Windows looks in System32 before any folder of ours, and a PC's own Visual C++ runtime there may be older than
/// the one ONNX Runtime was built for (a library built with Visual Studio 17.10 or later crashes on an older
/// msvcp140.dll). The library stays loaded, so the load ONNX Runtime's own loader then makes finds it in the process.
#[cfg(windows)]
fn load_beside(library: &PathBuf) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    unsafe extern "system" {
        fn LoadLibraryExW(name: *const u16, file: *mut std::ffi::c_void, flags: u32) -> *mut std::ffi::c_void;
    }
    const LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR: u32 = 0x100;
    const LOAD_LIBRARY_SEARCH_DEFAULT_DIRS: u32 = 0x1000;
    let name: Vec<u16> = library.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
    let loaded = unsafe { LoadLibraryExW(name.as_ptr(), std::ptr::null_mut(), LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS) };
    if loaded.is_null() {
        return Err(format!("{}: {}", library.display(), std::io::Error::last_os_error()));
    }
    Ok(())
}

#[cfg(not(windows))]
fn load_beside(_library: &PathBuf) -> Result<(), String> {
    Ok(()) // elsewhere a library names where its own libraries are
}

/// ONNX Runtime, loaded once a process from the library file given first (an absolute path).
fn runtime(library: &PathBuf) -> PyResult<&'static Environment> {
    RUNTIME
        .get_or_init(|| {
            load_beside(library)?;
            ort::init_from(library).map_err(|e| e.to_string())?.with_name("wozmod").build().map_err(|e| e.to_string())
        })
        .as_ref()
        .map_err(|e| PyRuntimeError::new_err(format!("ONNX Runtime did not load: {e}")))
}

/// A tensor of separator.weights: (name, how it is stored: "q4" or "f32", its shape, where its bytes start, where
/// a q4 tensor's scales start).
pub type Stored = (String, String, Vec<usize>, usize, usize);

const BLOCK: usize = 64;

/// A stored tensor's values, row by row.
pub fn stored_values(weights: &[u8], stored: &Stored) -> Result<Vec<f32>, String> {
    let (name, kind, shape, at, scales) = stored;
    let count: usize = shape.iter().product();
    let short = || format!("separator.weights ends before {name} does");
    if kind == "f32" {
        return Ok(floats(weights.get(*at..at + 4 * count).ok_or_else(short)?));
    }
    // 4-bit blocks of BLOCK down each column (MatMulNBits): value = (nibble - 8) * the block's scale
    let (rows, columns) = (shape[0], shape[1]);
    let blocks = rows.div_ceil(BLOCK);
    let packed = weights.get(*at..at + columns * blocks * BLOCK / 2).ok_or_else(short)?;
    let scale = floats(weights.get(*scales..scales + 4 * columns * blocks).ok_or_else(short)?);
    let mut out = vec![0f32; count];
    for column in 0..columns {
        for row in 0..rows {
            let byte = packed[(column * blocks * BLOCK + row) / 2];
            let nibble = if row % 2 == 0 { byte & 15 } else { byte >> 4 };
            out[row * columns + column] = (nibble as f32 - 8.0) * scale[column * blocks + row / BLOCK];
        }
    }
    Ok(out)
}

/// The network on the CPU, run by ONNX Runtime: spectra in, masks out.
#[pyclass]
pub struct SeparatorNetwork {
    session: Mutex<Session>,
}

#[pymethods]
impl SeparatorNetwork {
    /// SeparatorNetwork(runtime, graph, threads): runtime is ONNX Runtime's library file, graph the .onnx file
    /// (separator.weights beside it), threads the CPU threads its kernels use.
    #[new]
    fn new(py: Python<'_>, runtime_library: PathBuf, graph: PathBuf, threads: usize) -> PyResult<Self> {
        let environment = runtime(&runtime_library)?;
        py.detach(|| -> Result<Self, String> {
            // The graph's sizes are free: no buffer plan to remember, each tensor freed at its last use.
            let session = Session::builder(environment)
                .map_err(|e| e.to_string())?
                .with_intra_threads(threads)
                .map_err(|e| e.to_string())?
                .with_inter_threads(1)
                .map_err(|e| e.to_string())?
                .with_memory_pattern(false)
                .map_err(|e| e.to_string())?
                .commit_from_file(&graph)
                .map_err(|e| e.to_string())?;
            Ok(SeparatorNetwork { session: Mutex::new(session) })
        })
        .map_err(PyRuntimeError::new_err)
    }

    /// run(spec, chunks, frames) -> the masks (chunks x frames x width 32-bit floats) of chunks spectra, one after
    /// another in spec, of frames frames each.
    fn run<'py>(&self, py: Python<'py>, spec: &[u8], chunks: usize, frames: usize) -> PyResult<Bound<'py, PyBytes>> {
        if spec.len() != chunks * ROWS * frames * 2 * 4 {
            return Err(PyValueError::new_err("the spectra do not have that many chunks and frames"));
        }
        let spec = floats(spec);
        let masks = py
            .detach(|| -> Result<Vec<u8>, String> {
                let mut session = self.session.lock().map_err(|_| "an earlier run of the network panicked".to_string())?;
                let input = TensorRef::from_array_view(([chunks, ROWS, frames, 2], spec.as_slice())).map_err(|e| e.to_string())?;
                let outputs = session.run(ort::inputs!["spec" => input]).map_err(|e| e.to_string())?;
                let (_, masks) = outputs["masks"].try_extract_tensor::<f32>().map_err(|e| e.to_string())?;
                Ok(bytes(masks))
            })
            .map_err(PyRuntimeError::new_err)?;
        Ok(PyBytes::new(py, &masks))
    }
}
