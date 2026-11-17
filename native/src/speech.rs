//! The phones of English speech, from PocketSphinx (built from source by build.rs): each line force-aligned to the
//! words it says, or, for a line whose words are not known, the phones its all-phone recogniser hears.
//!
//! Each thread keeps one decoder for all the lines it takes: loading the model costs more than decoding a line. A
//! line's phones must not depend on the lines decoded before it, or the mouths would change with which lines a
//! player's discs hold and how they are split among threads. Unpatched, they do (of 30 lines, a fresh decoder matched
//! a reused one on 28 aligned and on 1 heard): each utterance's top-N Gaussian search starts from the last frame of the
//! one before, which patches/ptm_mgau-reset-topn.patch resets to the model's defaults.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use std::ffi::{CStr, CString, c_char, c_double, c_int, c_void};
use std::path::Path;
use std::sync::Once;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;

/// Samples a second of the speech PocketSphinx's en-us model is made for.
pub const RATE: usize = 16_000;

#[repr(C)]
struct Config {
    _opaque: [u8; 0],
}
#[repr(C)]
struct Decoder {
    _opaque: [u8; 0],
}
#[repr(C)]
struct Seg {
    _opaque: [u8; 0],
}
#[repr(C)]
struct Iter {
    _opaque: [u8; 0],
}
#[repr(C)]
struct Alignment {
    _opaque: [u8; 0],
}

unsafe extern "C" {
    fn ps_config_init(defn: *const c_void) -> *mut Config;
    fn ps_config_free(config: *mut Config) -> c_int;
    fn ps_config_set_str(config: *mut Config, name: *const c_char, val: *const c_char) -> *const c_void;
    fn ps_config_set_bool(config: *mut Config, name: *const c_char, val: c_int) -> *const c_void;
    fn ps_config_set_float(config: *mut Config, name: *const c_char, val: c_double) -> *const c_void;
    fn ps_init(config: *mut Config) -> *mut Decoder;
    fn ps_free(ps: *mut Decoder) -> c_int;
    fn ps_add_word(ps: *mut Decoder, word: *const c_char, phones: *const c_char, update: c_int) -> c_int;
    fn ps_lookup_word(ps: *mut Decoder, word: *const c_char) -> *mut c_char;
    fn ckd_free(ptr: *mut c_void);
    fn ps_set_align_text(ps: *mut Decoder, words: *const c_char) -> c_int;
    fn ps_set_alignment(ps: *mut Decoder, al: *mut Alignment) -> c_int;
    fn ps_get_alignment(ps: *mut Decoder) -> *mut Alignment;
    fn ps_start_stream(ps: *mut Decoder) -> c_int;
    fn ps_start_utt(ps: *mut Decoder) -> c_int;
    fn ps_process_raw(ps: *mut Decoder, data: *const i16, n: usize, no_search: c_int, full_utt: c_int) -> c_int;
    fn ps_end_utt(ps: *mut Decoder) -> c_int;
    fn ps_seg_iter(ps: *mut Decoder) -> *mut Seg;
    fn ps_seg_next(seg: *mut Seg) -> *mut Seg;
    fn ps_seg_word(seg: *mut Seg) -> *const c_char;
    fn ps_seg_frames(seg: *mut Seg, start: *mut c_int, end: *mut c_int);
    fn ps_alignment_words(al: *mut Alignment) -> *mut Iter;
    fn ps_alignment_iter_children(itor: *mut Iter) -> *mut Iter;
    fn ps_alignment_iter_next(itor: *mut Iter) -> *mut Iter;
    fn ps_alignment_iter_name(itor: *mut Iter) -> *const c_char;
    fn ps_alignment_iter_seg(itor: *mut Iter, start: *mut c_int, duration: *mut c_int) -> c_int;
    fn err_set_loglevel(level: c_int) -> c_int;
}

// The C maths library, for PocketSphinx (csrc/ps_math.h): the libm crate's functions are IEEE arithmetic only, so they
// give the same bits on every platform.
macro_rules! maths {
    ($($c:ident $cf:ident = $f:ident $ff:ident;)*) => {$(
        #[unsafe(no_mangle)]
        extern "C" fn $c(x: f64) -> f64 { libm::$f(x) }
        #[unsafe(no_mangle)]
        extern "C" fn $cf(x: f32) -> f32 { libm::$ff(x) }
    )*};
}
maths! {
    wozmod_ps_log wozmod_ps_logf = log logf;
    wozmod_ps_log10 wozmod_ps_log10f = log10 log10f;
    wozmod_ps_log2 wozmod_ps_log2f = log2 log2f;
    wozmod_ps_log1p wozmod_ps_log1pf = log1p log1pf;
    wozmod_ps_exp wozmod_ps_expf = exp expf;
    wozmod_ps_exp2 wozmod_ps_exp2f = exp2 exp2f;
    wozmod_ps_expm1 wozmod_ps_expm1f = expm1 expm1f;
    wozmod_ps_cos wozmod_ps_cosf = cos cosf;
    wozmod_ps_sin wozmod_ps_sinf = sin sinf;
    wozmod_ps_tan wozmod_ps_tanf = tan tanf;
    wozmod_ps_atan wozmod_ps_atanf = atan atanf;
    wozmod_ps_acos wozmod_ps_acosf = acos acosf;
    wozmod_ps_asin wozmod_ps_asinf = asin asinf;
    wozmod_ps_cosh wozmod_ps_coshf = cosh coshf;
    wozmod_ps_sinh wozmod_ps_sinhf = sinh sinhf;
    wozmod_ps_tanh wozmod_ps_tanhf = tanh tanhf;
}
#[unsafe(no_mangle)]
extern "C" fn wozmod_ps_pow(x: f64, y: f64) -> f64 {
    libm::pow(x, y)
}
#[unsafe(no_mangle)]
extern "C" fn wozmod_ps_powf(x: f32, y: f32) -> f32 {
    libm::powf(x, y)
}
#[unsafe(no_mangle)]
extern "C" fn wozmod_ps_atan2(y: f64, x: f64) -> f64 {
    libm::atan2(y, x)
}
#[unsafe(no_mangle)]
extern "C" fn wozmod_ps_atan2f(y: f32, x: f32) -> f32 {
    libm::atan2f(y, x)
}

fn c(text: &str) -> CString {
    CString::new(text).expect("no NUL in a PocketSphinx setting")
}

/// The model folder's files: the en-us acoustic model, the CMU dictionary and the phone language model.
#[derive(Clone)]
struct Model {
    hmm: CString,
    dict: CString,
    phones: CString,
}

impl Model {
    fn new(folder: &str) -> PyResult<Model> {
        let path = |name: &str| {
            let full = Path::new(folder).join(name);
            full.to_str().map(c).ok_or_else(|| PyValueError::new_err(format!("{} is not UTF-8", full.display())))
        };
        Ok(Model { hmm: path("en-us")?, dict: path("cmudict-en-us.dict")?, phones: path("en-us-phone.lm.bin")? })
    }
}

/// What a decoder does: force-align a line to its words, or hear its phones with no words.
#[derive(Clone, Copy, PartialEq)]
enum Mode {
    Align,
    Hear,
}

/// A PocketSphinx decoder, freed when it goes out of scope.
struct Recogniser(*mut Decoder);

impl Recogniser {
    /// The experiment's settings (lips-phones/align.py): 16 kHz, batch CMN, no dither, no best-path pass; hearing
    /// uses context-independent phones with Rhubarb Lip Sync's weights (language weight 0.8, beams 1e-20).
    fn new(model: &Model, mode: Mode) -> Result<Recogniser, String> {
        let failed = || format!("PocketSphinx could not load the speech model {}", model.hmm.to_string_lossy());
        static QUIET: Once = Once::new();
        QUIET.call_once(|| unsafe {
            err_set_loglevel(4); // ERR_FATAL: once, as PocketSphinx keeps the level in a global
        });
        unsafe {
            let config = ps_config_init(std::ptr::null());
            if config.is_null() {
                return Err(failed());
            }
            let set = |name: &str, value: &CString| ps_config_set_str(config, c(name).as_ptr(), value.as_ptr());
            set("hmm", &model.hmm);
            set("dict", &model.dict);
            ps_config_set_str(config, c("loglevel").as_ptr(), std::ptr::null());
            ps_config_set_float(config, c("samprate").as_ptr(), RATE as f64);
            set("cmn", &c("batch"));
            ps_config_set_bool(config, c("dither").as_ptr(), 0);
            ps_config_set_bool(config, c("bestpath").as_ptr(), 0);
            if mode == Mode::Hear {
                set("allphone", &model.phones);
                ps_config_set_bool(config, c("allphone_ci").as_ptr(), 1);
                ps_config_set_float(config, c("lw").as_ptr(), 0.8);
                ps_config_set_float(config, c("beam").as_ptr(), 1e-20);
                ps_config_set_float(config, c("pbeam").as_ptr(), 1e-20);
            }
            let ps = ps_init(config);
            ps_config_free(config); // the decoder keeps its own reference
            if ps.is_null() { Err(failed()) } else { Ok(Recogniser(ps)) }
        }
    }

    /// Starts a line as a fresh decoder would: the noise remover forgets the lines before (it keeps its estimate
    /// from utterance to utterance, for live audio).
    fn new_line(&self) {
        unsafe {
            ps_start_stream(self.0);
        }
    }

    fn decode(&self, samples: &[i16]) -> bool {
        unsafe {
            ps_start_utt(self.0) >= 0
                && ps_process_raw(self.0, samples.as_ptr(), samples.len(), 0, 1) >= 0
                && ps_end_utt(self.0) >= 0
        }
    }

    fn has(&self, word: &CStr) -> bool {
        unsafe {
            let found = ps_lookup_word(self.0, word.as_ptr());
            if found.is_null() {
                return false;
            }
            ckd_free(found.cast());
            true
        }
    }
}

impl Drop for Recogniser {
    fn drop(&mut self) {
        unsafe {
            ps_free(self.0);
        }
    }
}

fn name(text: *const c_char) -> String {
    unsafe { CStr::from_ptr(text) }.to_string_lossy().into_owned()
}

/// A phone, its first 10 ms frame and its length in frames.
type Phone = (String, i32, i32);

/// The phones of samples aligned to words (each word in the dictionary, or in spelled with its phones), or None
/// when PocketSphinx cannot align them.
fn align_with(decoder: &Recogniser, samples: &[i16], words: &[String], spelled: &[(String, String)]) -> Option<Vec<Phone>> {
    for (word, phones) in spelled {
        let (word, phones) = (CString::new(word.as_str()).ok()?, CString::new(phones.as_str()).ok()?);
        if !decoder.has(&word) && unsafe { ps_add_word(decoder.0, word.as_ptr(), phones.as_ptr(), 1) } < 0 {
            return None;
        }
    }
    let text = CString::new(words.join(" ")).ok()?;
    if words.is_empty() || unsafe { ps_set_align_text(decoder.0, text.as_ptr()) } < 0 || !decoder.decode(samples) {
        return None;
    }
    if unsafe { ps_set_alignment(decoder.0, std::ptr::null_mut()) } < 0 || !decoder.decode(samples) {
        return None;
    }
    let alignment = unsafe { ps_get_alignment(decoder.0) };
    if alignment.is_null() {
        return None;
    }
    let mut out = Vec::new();
    unsafe {
        let mut word = ps_alignment_words(alignment);
        while !word.is_null() {
            let mut phone = ps_alignment_iter_children(word);
            while !phone.is_null() {
                let (mut start, mut length) = (0, 0);
                ps_alignment_iter_seg(phone, &mut start, &mut length);
                out.push((name(ps_alignment_iter_name(phone)), start, length));
                phone = ps_alignment_iter_next(phone);
            }
            word = ps_alignment_iter_next(word);
        }
    }
    Some(out)
}

/// The phones the all-phone recogniser hears in samples (silences and noises by their filler names), None when it
/// hears nothing.
fn heard(decoder: &Recogniser, samples: &[i16]) -> Option<Vec<Phone>> {
    if !decoder.decode(samples) {
        return None;
    }
    let mut out = Vec::new();
    unsafe {
        let mut seg = ps_seg_iter(decoder.0);
        while !seg.is_null() {
            let (mut start, mut end) = (0, 0);
            ps_seg_frames(seg, &mut start, &mut end);
            out.push((name(ps_seg_word(seg)), start, end - start + 1));
            seg = ps_seg_next(seg);
        }
    }
    Some(out)
}

fn samples(data: &[u8]) -> PyResult<Vec<i16>> {
    if data.len() % 2 != 0 {
        return Err(PyValueError::new_err(format!("{} bytes is not a whole number of 16-bit samples", data.len())));
    }
    Ok(data.chunks_exact(2).map(|c| i16::from_le_bytes([c[0], c[1]])).collect())
}

/// Runs job on every item over threads threads, each thread with a decoder of its own in mode that serves every line
/// it takes, the results in the items' order; an error when PocketSphinx cannot load the model.
fn each<T: Sync, R: Send>(
    items: &[T],
    threads: usize,
    model: &Model,
    mode: Mode,
    job: impl Fn(&Recogniser, &T) -> R + Sync,
) -> Result<Vec<R>, String> {
    let next = AtomicUsize::new(0);
    let done: Result<Vec<Vec<(usize, R)>>, String> = thread::scope(|scope| {
        let workers: Vec<_> = (0..threads.clamp(1, items.len().max(1)))
            .map(|_| {
                scope.spawn(|| {
                    let (mut decoder, mut mine) = (None, Vec::new());
                    loop {
                        let at = next.fetch_add(1, Ordering::Relaxed);
                        let Some(item) = items.get(at) else { break Ok(mine) };
                        let decoder = match &mut decoder {
                            Some(decoder) => decoder,
                            empty => empty.insert(Recogniser::new(model, mode)?),
                        };
                        decoder.new_line();
                        mine.push((at, job(decoder, item)));
                    }
                })
            })
            .collect();
        workers.into_iter().map(|w| w.join().expect("a speech thread panicked")).collect()
    });
    let mut done: Vec<(usize, R)> = done?.into_iter().flatten().collect();
    done.sort_by_key(|(at, _)| *at);
    Ok(done.into_iter().map(|(_, r)| r).collect())
}

/// Per line, the phones of its speech force-aligned to its words, as (phone, first 10 ms frame, frames), or None
/// when it cannot be aligned.
///
/// model is the folder of the en-us acoustic model (en-us/), cmudict-en-us.dict and en-us-phone.lm.bin. Each line
/// is (16-bit little-endian mono samples at 16 kHz, its words, and (word, space-separated phones) for each of its
/// words the dictionary may lack). threads decode lines side by side; the result does not depend on it.
#[pyfunction]
pub fn aligned_phones(
    py: Python<'_>,
    model: &str,
    lines: Vec<(PyBackedBytes, Vec<String>, Vec<(String, String)>)>,
    threads: usize,
) -> PyResult<Vec<Option<Vec<Phone>>>> {
    let model = Model::new(model)?;
    let mut work = Vec::with_capacity(lines.len());
    for (speech, words, spelled) in &lines {
        work.push((samples(speech)?, words.clone(), spelled.clone()));
    }
    py.detach(|| each(&work, threads, &model, Mode::Align, |decoder, (s, w, p)| align_with(decoder, s, w, p)))
        .map_err(PyValueError::new_err)
}

/// Per line (16-bit little-endian mono samples at 16 kHz), the phones PocketSphinx's all-phone recogniser hears, as
/// (phone or filler, first 10 ms frame, frames), or None when it hears nothing. model and threads as for
/// aligned_phones.
#[pyfunction]
pub fn heard_phones(py: Python<'_>, model: &str, lines: Vec<PyBackedBytes>, threads: usize) -> PyResult<Vec<Option<Vec<Phone>>>> {
    let model = Model::new(model)?;
    let work = lines.iter().map(|s| samples(s)).collect::<PyResult<Vec<_>>>()?;
    py.detach(|| each(&work, threads, &model, Mode::Hear, |decoder, s| heard(decoder, s))).map_err(PyValueError::new_err)
}
