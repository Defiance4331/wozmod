//! Mouth shapes from speech: a model of what each lip frame sounds like, fitted to the lines whose mouth Koei keyed
//! for that very clip, then a path through the shapes of a line that holds each shape about as long as Koei's
//! animators hold theirs, changing where the sound does.
//!
//! The install learns it from the player's own discs, so the lip bank must come out byte for byte the same on every
//! run and machine: the arithmetic here is only IEEE + - * / and sqrt (correctly rounded everywhere), summed in a
//! fixed order on one thread, with its own FFT, cosines and logarithm instead of the platform's libm or rustfft's SIMD
//! paths, whose last bits differ between machines.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::types::PyBytes;
use std::collections::HashMap;
use std::f64::consts::{LN_2, PI, SQRT_2};

/// Samples a second of the speech (16-bit mono), and per lip frame (30 a second).
const RATE: usize = 24_000;
const HOP: usize = RATE / 30;
/// The spectrum's window, 43 ms centred on the lip frame's middle: long enough to resolve the vowel's formants.
const WIN: usize = 1024;
/// Mel bands from 80 Hz to 7 kHz, which span the first three formants and the fricatives.
const BANDS: usize = 24;
const LOW_HZ: f64 = 80.0;
const HIGH_HZ: f64 = 7_000.0;
/// Lip frames either side whose bands and band changes each frame's features also hold.
const CONTEXT: usize = 2;
/// Per frame: (bands, changes) for each of the 2 * CONTEXT + 1 frames, then the frame's mean band level.
const WIDTH: usize = 2 * BANDS * (2 * CONTEXT + 1) + 1;
/// Mouth shapes per set (0 shut); a label of UNLABELLED is a frame left out of the fit.
const SHAPES: usize = 10;
const UNLABELLED: u8 = 255;
/// The share of the mean variance added to every feature's variance, so a feature that barely varies in the
/// training lines cannot dominate the model.
const SHRINK: f64 = 0.01;

fn taylor(x: f64) -> (f64, f64) {
    let (mut cos, mut sin, mut c, mut s) = (0.0, 0.0, 1.0, x);
    for k in 0..12 {
        cos += c;
        sin += s;
        let n = (2 * k + 1) as f64;
        c *= -x * x / (n * (n + 1.0));
        s *= -x * x / ((n + 1.0) * (n + 2.0));
    }
    (cos, sin)
}

/// cos and sin of 2 pi j / WIN for j in 0..WIN, from series on the first eighth of the circle and its symmetries.
fn circle() -> (Vec<f64>, Vec<f64>) {
    let (mut cos, mut sin) = (vec![0.0; WIN], vec![0.0; WIN]);
    for j in 0..=WIN / 8 {
        (cos[j], sin[j]) = taylor(2.0 * PI * j as f64 / WIN as f64);
    }
    for j in WIN / 8 + 1..=WIN / 4 {
        (cos[j], sin[j]) = (sin[WIN / 4 - j], cos[WIN / 4 - j]);
    }
    for j in WIN / 4 + 1..=WIN / 2 {
        (cos[j], sin[j]) = (-cos[WIN / 2 - j], sin[WIN / 2 - j]);
    }
    for j in WIN / 2 + 1..WIN {
        (cos[j], sin[j]) = (cos[WIN - j], -sin[WIN - j]);
    }
    (cos, sin)
}

/// The natural logarithm of a positive normal number: its binary exponent, and a series on the mantissa.
fn ln(x: f64) -> f64 {
    let bits = x.to_bits();
    let mut exponent = ((bits >> 52) & 0x7FF) as i64 - 1023;
    let mut m = f64::from_bits(bits & 0x000F_FFFF_FFFF_FFFF | 0x3FF0_0000_0000_0000);
    if m > SQRT_2 {
        m /= 2.0;
        exponent += 1;
    }
    let s = (m - 1.0) / (m + 1.0); // |s| <= 0.172: ln m = 2 (s + s^3 / 3 + ...)
    let (mut sum, mut power) = (0.0, s);
    for k in 0..12 {
        sum += power / (2 * k + 1) as f64;
        power *= s * s;
    }
    exponent as f64 * LN_2 + 2.0 * sum
}

fn exp(x: f64) -> f64 {
    let k = (x / LN_2).round();
    let r = x - k * LN_2;
    let (mut sum, mut term) = (1.0, 1.0);
    for i in 1..24 {
        term *= r / i as f64;
        sum += term;
    }
    sum * f64::from_bits(((k as i64 + 1023) as u64) << 52)
}

struct Spectrum {
    cos: Vec<f64>,
    sin: Vec<f64>,
    window: Vec<f64>,
    /// Per band, (FFT bin, weight) of its triangle on the mel scale.
    bands: Vec<Vec<(usize, f64)>>,
}

impl Spectrum {
    fn new() -> Self {
        let (cos, sin) = circle();
        let window = (0..WIN).map(|n| 0.5 - 0.5 * cos[n]).collect(); // a periodic Hann window
        let mel = |hz: f64| ln(1.0 + hz / 700.0);
        let (low, high) = (mel(LOW_HZ), mel(HIGH_HZ));
        let edges: Vec<f64> = (0..BANDS + 2)
            .map(|i| 700.0 * (exp(low + (high - low) * i as f64 / (BANDS + 1) as f64) - 1.0))
            .collect();
        let bands = (0..BANDS)
            .map(|b| {
                let (lo, mid, hi) = (edges[b], edges[b + 1], edges[b + 2]);
                (0..=WIN / 2)
                    .filter_map(|bin| {
                        let hz = (bin * RATE) as f64 / WIN as f64;
                        let weight = ((hz - lo) / (mid - lo)).min((hi - hz) / (hi - mid));
                        (weight > 0.0).then_some((bin, weight))
                    })
                    .collect()
            })
            .collect();
        Spectrum { cos, sin, window, bands }
    }

    /// The power at each bin 0..=WIN/2 of a windowed frame: an in-place radix-2 FFT.
    fn power(&self, frame: &[f64]) -> Vec<f64> {
        let (mut re, mut im) = (vec![0.0; WIN], vec![0.0; WIN]);
        let bits = WIN.trailing_zeros();
        for (n, &x) in frame.iter().enumerate() {
            re[n.reverse_bits() >> (usize::BITS - bits)] = x * self.window[n];
        }
        let mut size = 2;
        while size <= WIN {
            let stride = WIN / size;
            for start in (0..WIN).step_by(size) {
                for k in 0..size / 2 {
                    let (c, s) = (self.cos[k * stride], -self.sin[k * stride]);
                    let (a, b) = (start + k, start + k + size / 2);
                    let (tr, ti) = (re[b] * c - im[b] * s, re[b] * s + im[b] * c);
                    (re[b], im[b]) = (re[a] - tr, im[a] - ti);
                    (re[a], im[a]) = (re[a] + tr, im[a] + ti);
                }
            }
            size *= 2;
        }
        (0..=WIN / 2).map(|k| re[k] * re[k] + im[k] * im[k]).collect()
    }
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    let n = values.len();
    if n % 2 == 1 { values[n / 2] } else { (values[n / 2 - 1] + values[n / 2]) / 2.0 }
}

/// A line's speech: its features and which of its lip frames are silent.
struct Line {
    features: Vec<[f64; WIDTH]>,
    silent: Vec<bool>,
}

fn pcm(data: &[u8], what: &str) -> PyResult<Vec<f64>> {
    if data.len() % 2 != 0 {
        return Err(PyValueError::new_err(format!("{what}: {} bytes is not a whole number of 16-bit samples", data.len())));
    }
    Ok(data.chunks_exact(2).map(|c| f64::from(i16::from_le_bytes([c[0], c[1]])) / 32768.0).collect())
}

/// Per lip frame 0..frames: the log mel bands of the window on its middle, less the line's median of each band
/// (the speaker's and the recording's own colour), their change to the next frame, the same for CONTEXT frames either
/// side (the first and last repeated past the ends), and the mean band level. A frame whose mean square is under
/// silence (of full scale) is silent.
fn line(spectrum: &Spectrum, samples: &[f64], frames: usize, silence: f64) -> Line {
    let mut bands = vec![[0.0f64; BANDS]; frames];
    let mut window = vec![0.0; WIN];
    for (t, row) in bands.iter_mut().enumerate() {
        let start = (t * HOP + HOP / 2) as isize - (WIN / 2) as isize;
        for (n, slot) in window.iter_mut().enumerate() {
            let at = start + n as isize;
            *slot = if at >= 0 && (at as usize) < samples.len() { samples[at as usize] } else { 0.0 };
        }
        let power = spectrum.power(&window);
        for (value, triangle) in row.iter_mut().zip(&spectrum.bands) {
            *value = ln(triangle.iter().map(|&(bin, weight)| weight * power[bin]).sum::<f64>() + 1e-10);
        }
    }
    for b in 0..BANDS {
        let centre = median(&mut bands.iter().map(|row| row[b]).collect::<Vec<_>>());
        for row in bands.iter_mut() {
            row[b] -= centre;
        }
    }
    let change = |t: usize| -> [f64; BANDS] {
        let mut out = [0.0; BANDS];
        if t + 1 < frames {
            for b in 0..BANDS {
                out[b] = bands[t + 1][b] - bands[t][b];
            }
        }
        out
    };
    let features = (0..frames)
        .map(|t| {
            let mut row = [0.0; WIDTH];
            for i in 0..=2 * CONTEXT {
                let at = (t + i).saturating_sub(CONTEXT).min(frames - 1);
                row[2 * BANDS * i..2 * BANDS * i + BANDS].copy_from_slice(&bands[at]);
                row[2 * BANDS * i + BANDS..2 * BANDS * (i + 1)].copy_from_slice(&change(at));
            }
            row[WIDTH - 1] = bands[t].iter().sum::<f64>() / BANDS as f64;
            row
        })
        .collect();
    let silent = (0..frames)
        .map(|t| {
            let part = &samples[(t * HOP).min(samples.len())..((t + 1) * HOP).min(samples.len())];
            part.iter().map(|x| x * x).sum::<f64>() < silence * part.len() as f64 || part.is_empty()
        })
        .collect();
    Line { features, silent }
}

/// Linear discriminant analysis: per shape, a mean, and one covariance shared by all shapes. scores(x)[k] is the
/// log of shape k's likelihood times its share of the training frames, up to a term the same for every shape.
struct Model {
    centre: Vec<f64>,
    scale: Vec<f64>,
    weights: Vec<[f64; SHAPES]>,
    bias: [f64; SHAPES],
}

impl Model {
    fn fit(rows: &[&[f64; WIDTH]], labels: &[usize]) -> Model {
        let n = rows.len() as f64;
        let mut centre = vec![0.0; WIDTH];
        let mut scale = vec![0.0; WIDTH];
        for row in rows {
            for (c, x) in centre.iter_mut().zip(row.iter()) {
                *c += x;
            }
        }
        centre.iter_mut().for_each(|c| *c /= n);
        for row in rows {
            for ((s, c), x) in scale.iter_mut().zip(&centre).zip(row.iter()) {
                *s += (x - c) * (x - c);
            }
        }
        scale.iter_mut().for_each(|s| *s = (*s / n).sqrt() + 1e-6);
        let z: Vec<Vec<f64>> =
            rows.iter().map(|row| row.iter().zip(&centre).zip(&scale).map(|((x, c), s)| (x - c) / s).collect()).collect();
        let mut counts = [0usize; SHAPES];
        let mut means = vec![vec![0.0; WIDTH]; SHAPES];
        for (row, &k) in z.iter().zip(labels) {
            counts[k] += 1;
            for (m, x) in means[k].iter_mut().zip(row) {
                *m += x;
            }
        }
        for (mean, &count) in means.iter_mut().zip(&counts) {
            mean.iter_mut().for_each(|m| *m /= count.max(1) as f64);
        }
        // The covariance of each frame about its shape's mean, lower triangle.
        let mut cov = vec![vec![0.0; WIDTH]; WIDTH];
        let mut d = vec![0.0; WIDTH];
        for (row, &k) in z.iter().zip(labels) {
            for (slot, (x, m)) in d.iter_mut().zip(row.iter().zip(&means[k])) {
                *slot = x - m;
            }
            for i in 0..WIDTH {
                for j in 0..=i {
                    cov[i][j] += d[i] * d[j];
                }
            }
        }
        let trace = (0..WIDTH).map(|i| cov[i][i] / n).sum::<f64>();
        for i in 0..WIDTH {
            for j in 0..=i {
                cov[i][j] /= n;
            }
            cov[i][i] += SHRINK * trace / WIDTH as f64;
        }
        // Cholesky: cov = L L^T, in place in the lower triangle.
        for j in 0..WIDTH {
            let mut diagonal = cov[j][j];
            for k in 0..j {
                diagonal -= cov[j][k] * cov[j][k];
            }
            cov[j][j] = diagonal.sqrt();
            for i in j + 1..WIDTH {
                let mut value = cov[i][j];
                for k in 0..j {
                    value -= cov[i][k] * cov[j][k];
                }
                cov[i][j] = value / cov[j][j];
            }
        }
        let mut weights = vec![[0.0; SHAPES]; WIDTH];
        let mut bias = [f64::NEG_INFINITY; SHAPES];
        for k in (0..SHAPES).filter(|&k| counts[k] > 0) {
            let mut w = means[k].clone(); // solve cov w = mean_k: forward through L, then back through L^T
            for i in 0..WIDTH {
                for j in 0..i {
                    w[i] -= cov[i][j] * w[j];
                }
                w[i] /= cov[i][i];
            }
            for i in (0..WIDTH).rev() {
                for j in i + 1..WIDTH {
                    w[i] -= cov[j][i] * w[j];
                }
                w[i] /= cov[i][i];
            }
            let quadratic = w.iter().zip(&means[k]).map(|(a, b)| a * b).sum::<f64>();
            bias[k] = ln(counts[k] as f64 / n) - quadratic / 2.0;
            for (row, value) in weights.iter_mut().zip(&w) {
                row[k] = *value;
            }
        }
        Model { centre, scale, weights, bias }
    }

    /// Per frame of the line, each shape's score; a silent frame allows only shut.
    fn scores(&self, line: &Line) -> Vec<[f64; SHAPES]> {
        line.features
            .iter()
            .zip(&line.silent)
            .map(|(row, &silent)| {
                let mut out = if silent { [f64::NEG_INFINITY; SHAPES] } else { self.bias };
                if silent {
                    out[0] = 0.0;
                    return out;
                }
                for ((x, (c, s)), w) in row.iter().zip(self.centre.iter().zip(&self.scale)).zip(&self.weights) {
                    let z = (x - c) / s;
                    for k in 0..SHAPES {
                        out[k] += z * w[k];
                    }
                }
                out
            })
            .collect()
    }
}

/// Phones a lip frame is told apart by: 0 for silence or a noise, 1 to 39 the CMU phones (voices/phones.py). Each frame
/// of a line with phones carries three: the phone sounding at the frame's middle, and the phones before and after it.
const PHONES: u8 = 40;
/// The pull of each level of the phone table towards the level below it, as a count of frames (pseudo-count a). A
/// design constant, chosen with the weights below on the even voice ids alone (fitted on one half, decoded on the
/// other), from 2, 10 and 50.
const PULL: f64 = 10.0;
/// The weight of log P(shape | phones) against the frame scores, for phones aligned to a line's words and for phones
/// heard with no words (chosen as PULL, from 0.25 to 8). Judged once on the odd ids against the scores alone: Koei's
/// exact shape on 0.390 -> 0.489 of a line's speaking frames (aligned; Wilcoxon p 8e-26, 81% of lines better) and
/// 0.390 -> 0.431 (heard; p 2e-14), with the changes of shape as near Koei's as before (F1 p 0.67 and 0.41).
const ALIGNED: f64 = 2.0;
const HEARD: f64 = 1.0;

/// P(shape | previous phone, phone, next phone) over the known lines' labelled, sounding frames, each level of context
/// pulled towards the one below with PULL: the shapes' shares (add-one), then the phone alone, then (phone, next), then
/// (previous, phone, next). A context the known lines never hold stops at the level below it.
struct Table {
    phone: Vec<[f64; SHAPES]>,
    next: HashMap<[u8; 2], [f64; SHAPES]>,
    both: HashMap<[u8; 3], [f64; SHAPES]>,
}

fn pulled(counts: &[f64; SHAPES], below: &[f64; SHAPES]) -> [f64; SHAPES] {
    let total = counts.iter().sum::<f64>() + PULL;
    let mut out = [0.0; SHAPES];
    for k in 0..SHAPES {
        out[k] = (counts[k] + PULL * below[k]) / total;
    }
    out
}

impl Table {
    fn learn(rows: &[([u8; 3], usize)]) -> Table {
        let (mut all, mut phone) = ([0.0; SHAPES], vec![[0.0; SHAPES]; usize::from(PHONES)]);
        let (mut next, mut both) = (HashMap::new(), HashMap::new());
        for &([before, now, after], shape) in rows {
            all[shape] += 1.0;
            phone[usize::from(now)][shape] += 1.0;
            next.entry([now, after]).or_insert([0.0; SHAPES])[shape] += 1.0;
            both.entry([before, now, after]).or_insert([0.0; SHAPES])[shape] += 1.0;
        }
        let total = all.iter().sum::<f64>() + SHAPES as f64;
        let mut shares = [0.0; SHAPES];
        for k in 0..SHAPES {
            shares[k] = (all[k] + 1.0) / total;
        }
        Table { phone: phone.iter().map(|counts| pulled(counts, &shares)).collect(), next, both }
    }

    /// log P(shape | context) per shape.
    fn log(&self, [before, now, after]: [u8; 3]) -> [f64; SHAPES] {
        let mut p = self.phone[usize::from(now)];
        if let Some(counts) = self.next.get(&[now, after]) {
            p = pulled(counts, &p);
        }
        if let Some(counts) = self.both.get(&[before, now, after]) {
            p = pulled(counts, &p);
        }
        p.map(ln)
    }
}

/// A line's phone context per lip frame, from 3 bytes a frame.
fn contexts(data: &[u8], frames: usize, what: &str) -> Result<Vec<[u8; 3]>, String> {
    if data.len() != 3 * frames {
        return Err(format!("{what}: {} bytes of phones for {frames} lip frames (3 a frame)", data.len()));
    }
    if let Some(bad) = data.iter().find(|&&p| p >= PHONES) {
        return Err(format!("{what}: phone {bad} is not 0 to {}", PHONES - 1));
    }
    Ok(data.chunks_exact(3).map(|c| [c[0], c[1], c[2]]).collect())
}

/// scores with weight * log P(shape | phones) added on each sounding frame (a silent frame stays shut).
fn with_phones(mut scores: Vec<[f64; SHAPES]>, silent: &[bool], phones: &[[u8; 3]], table: &Table, weight: f64) -> Vec<[f64; SHAPES]> {
    for ((row, &silent), &context) in scores.iter_mut().zip(silent).zip(phones) {
        if !silent {
            for (score, log) in row.iter_mut().zip(table.log(context)) {
                *score += weight * log;
            }
        }
    }
    scores
}

/// Ages a run of one shape is told apart by, in lip frames: a run CAP frames or longer has one chance of ending per
/// frame, pooled over every age from CAP on. Koei keys the mouth mostly 3 or 4 frames apart (of 5089 key intervals in
/// the even voice ids' blocks: 1 frame 0.004, 2 frames 0.085, 3 frames 0.453, 4 frames 0.206) and rarely holds one
/// shape past half a second; on the even lines' own folds, CAP 8, 16 and 30 move agreement less than 0.001.
const CAP: usize = 16;
/// The weight of a run length's log chance against the frame scores. A design constant, chosen on the even voice ids
/// alone (fitted on one half, decoded on the other): 0.35 changes shape as often as Koei (9.12 a speaking second,
/// Koei 9.13) with about as many A-B-A returns (0.53 a second, Koei 0.47), where 1.0 holds only 0.4% of open runs at
/// 1-2 frames (Koei 5%) and returns A-B-A 0.72 a second. Judged once on the odd ids against the change-cost path
/// before it: changes of shape within a frame of Koei's, F1 0.636 -> 0.662 a line (Wilcoxon p 3e-5).
const WEIGHT: f64 = 0.35;

/// How long runs of one shape last in the known lines' labels, shut and open apart: per age 1..=CAP, the log chance
/// (times WEIGHT) that a run this old ends at this frame, and that it goes on. A run cut off by the line's end or an
/// unlabelled frame counts as going on.
struct Runs {
    ends: [[f64; CAP]; 2],
    goes_on: [[f64; CAP]; 2],
}

impl Runs {
    fn learn<'a>(lines: impl Iterator<Item = &'a [u8]>) -> Runs {
        let (mut at_risk, mut ended) = ([[0usize; CAP]; 2], [[0usize; CAP]; 2]);
        for labels in lines {
            let mut start = 0;
            while start < labels.len() {
                let shape = labels[start];
                let end = start + labels[start..].iter().take_while(|&&l| l == shape).count();
                if shape != UNLABELLED {
                    let (class, n) = (usize::from(shape != 0), end - start);
                    for age in 1..=n.min(CAP - 1) {
                        at_risk[class][age - 1] += 1;
                    }
                    if n >= CAP {
                        at_risk[class][CAP - 1] += n - CAP + 1;
                    }
                    if end < labels.len() && labels[end] != UNLABELLED {
                        ended[class][n.min(CAP) - 1] += 1;
                    }
                }
                start = end;
            }
        }
        let log = |part: usize, of: usize| if part == 0 { f64::NEG_INFINITY } else { WEIGHT * ln(part as f64 / of as f64) };
        let (mut ends, mut goes_on) = ([[0.0; CAP]; 2], [[0.0; CAP]; 2]);
        for class in 0..2 {
            for age in 0..CAP {
                let (risk, end) = (at_risk[class][age], ended[class][age]);
                // No run reached this age: let it end, so the age costs nothing beyond its never being reached.
                (ends[class][age], goes_on[class][age]) = if risk == 0 { (0.0, f64::NEG_INFINITY) } else { (log(end, risk), log(risk - end, risk)) };
            }
        }
        Runs { ends, goes_on }
    }
}

/// The shape per frame on the best path through (shape, age of its run): each frame's score, plus the log chance of
/// each run lasting as long as it does (Viterbi on an explicit-duration model). Ties go to the older run staying
/// on, the youngest age ending, the lowest shape. None when no path is possible: the line's silences need a shut run
/// longer, or an open run ended sooner, than any the known lines hold.
fn path(scores: &[[f64; SHAPES]], runs: &Runs) -> Option<Vec<u8>> {
    let Some(first) = scores.first() else { return Some(Vec::new()) };
    let class = |k: usize| usize::from(k != 0);
    let mut total = [[f64::NEG_INFINITY; CAP]; SHAPES];
    for k in 0..SHAPES {
        total[k][0] = first[k];
    }
    let mut from = vec![[[0u16; CAP]; SHAPES]; scores.len()]; // the state before: shape * CAP + age
    for (t, frame) in scores.iter().enumerate().skip(1) {
        let mut leave = [(f64::NEG_INFINITY, 0usize); SHAPES]; // per shape, its best end and that run's age
        for k in 0..SHAPES {
            for age in 0..CAP {
                let score = total[k][age] + runs.ends[class(k)][age];
                if score > leave[k].0 {
                    leave[k] = (score, age);
                }
            }
        }
        let first = (0..SHAPES).fold(0, |b, k| if leave[k].0 > leave[b].0 { k } else { b });
        let second = (0..SHAPES).filter(|&k| k != first).fold(usize::from(first == 0), |b, k| if leave[k].0 > leave[b].0 { k } else { b });
        let mut next = [[f64::NEG_INFINITY; CAP]; SHAPES];
        for k in 0..SHAPES {
            let goes_on = &runs.goes_on[class(k)];
            for age in 1..CAP {
                next[k][age] = total[k][age - 1] + goes_on[age - 1];
                from[t][k][age] = (k * CAP + age - 1) as u16;
            }
            let held = total[k][CAP - 1] + goes_on[CAP - 1];
            if held > next[k][CAP - 1] {
                next[k][CAP - 1] = held;
                from[t][k][CAP - 1] = (k * CAP + CAP - 1) as u16;
            }
            let source = if k == first { second } else { first };
            next[k][0] = leave[source].0;
            from[t][k][0] = (source * CAP + leave[source].1) as u16;
            for age in 0..CAP {
                next[k][age] += frame[k];
            }
        }
        total = next;
    }
    let mut state = (0..SHAPES * CAP).fold(0, |b, s| if total[s / CAP][s % CAP] > total[b / CAP][b % CAP] { s } else { b });
    if total[state / CAP][state % CAP] == f64::NEG_INFINITY {
        return None;
    }
    let mut out = vec![0u8; scores.len()];
    for t in (0..scores.len()).rev() {
        out[t] = (state / CAP) as u8;
        state = usize::from(from[t][state / CAP][state % CAP]);
    }
    Some(out)
}

/// The shape of each lip frame of each line, learned from the known lines.
///
/// known holds (speech, labels, aligned, heard) per line whose mouth was keyed for its speech: 16-bit little-endian
/// mono samples at 24 kHz, one byte per lip frame, its mouth shape 0 to 9 (0 shut) or 255 to leave it out, and the
/// line's phones per lip frame (3 bytes a frame: before, now, after), aligned to its words and heard with none, or
/// None. lines holds (speech, lip frames, aligned, heard) per line to give a mouth; it takes its aligned phones, else
/// its heard ones, else the sound alone. A frame whose level is under silence (dB of full scale) is shut.
/// Each line's path holds each shape about as long as the known lines' labels hold theirs (Runs).
#[pyfunction]
pub fn lip_shapes<'py>(
    py: Python<'py>,
    known: Vec<(PyBackedBytes, PyBackedBytes, Option<PyBackedBytes>, Option<PyBackedBytes>)>,
    lines: Vec<(PyBackedBytes, usize, Option<PyBackedBytes>, Option<PyBackedBytes>)>,
    silence: f64,
) -> PyResult<Vec<Bound<'py, PyBytes>>> {
    type Phones = [Option<Vec<[u8; 3]>>; 2];
    let phones = |aligned: &Option<PyBackedBytes>, heard: &Option<PyBackedBytes>, frames: usize, what: &str| -> PyResult<Phones> {
        let read = |p: &Option<PyBackedBytes>| p.as_ref().map(|p| contexts(p, frames, what)).transpose();
        Ok([read(aligned).map_err(PyValueError::new_err)?, read(heard).map_err(PyValueError::new_err)?])
    };
    let mut examples = Vec::with_capacity(known.len());
    for (speech, labels, aligned, heard) in &known {
        if let Some(bad) = labels.iter().find(|&&l| usize::from(l) >= SHAPES && l != UNLABELLED) {
            return Err(PyValueError::new_err(format!("label {bad} is no shape 0 to {}", SHAPES - 1)));
        }
        let found = phones(aligned, heard, labels.len(), "known phones")?;
        examples.push((pcm(speech, "known speech")?, labels.to_vec(), found));
    }
    let mut wanted = Vec::with_capacity(lines.len());
    for (speech, frames, aligned, heard) in &lines {
        wanted.push((pcm(speech, "line speech")?, *frames, phones(aligned, heard, *frames, "line phones")?));
    }
    let shapes = py.detach(|| {
        let silence = exp(silence / 10.0 * ln(10.0)); // a mean square, of full scale
        let spectrum = Spectrum::new();
        let known: Vec<(Line, Vec<u8>, &Phones)> = examples
            .iter()
            .filter(|(_, labels, _)| !labels.is_empty())
            .map(|(samples, labels, found)| (line(&spectrum, samples, labels.len(), silence), labels.clone(), found))
            .collect();
        let (mut rows, mut targets) = (Vec::new(), Vec::new());
        let mut table_rows: [Vec<([u8; 3], usize)>; 2] = [Vec::new(), Vec::new()];
        for (speech, labels, found) in &known {
            for (t, (row, &label)) in speech.features.iter().zip(labels).enumerate() {
                if label != UNLABELLED {
                    rows.push(row);
                    targets.push(usize::from(label));
                    for (kind, contexts) in found.iter().enumerate() {
                        if let (Some(contexts), false) = (contexts, speech.silent[t]) {
                            table_rows[kind].push((contexts[t], usize::from(label)));
                        }
                    }
                }
            }
        }
        if rows.is_empty() {
            return Err("no known line has a labelled lip frame to learn the mouth shapes from");
        }
        let model = Model::fit(&rows, &targets);
        let runs = Runs::learn(known.iter().map(|(_, labels, _)| &labels[..]));
        let tables = [Table::learn(&table_rows[0]), Table::learn(&table_rows[1])];
        wanted
            .iter()
            .map(|(samples, frames, [aligned, heard])| {
                let speech = line(&spectrum, samples, *frames, silence);
                let scores = model.scores(&speech);
                let scores = match (aligned, heard) {
                    (Some(p), _) => with_phones(scores, &speech.silent, p, &tables[0], ALIGNED),
                    (None, Some(p)) => with_phones(scores, &speech.silent, p, &tables[1], HEARD),
                    (None, None) => scores,
                };
                path(&scores, &runs)
            })
            .collect::<Option<Vec<_>>>()
            .ok_or("the known lines hold no run of one shape that fits a line's silences")
    });
    let shapes = shapes.map_err(PyValueError::new_err)?;
    Ok(shapes.iter().map(|s| PyBytes::new(py, s)).collect())
}
