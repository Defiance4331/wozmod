//! Fast LZP2 and DXT5 codecs. Each function gives the same bytes as the Python codec in wozmod.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

const MAX_LITERAL: usize = 0x3F;
const MAX_MATCH: usize = 18;
const WINDOW: usize = 0x800;
const MAX_REPEAT: usize = 0x3F03;
const HASH_BITS: u32 = 16;

fn hash3(data: &[u8], at: usize) -> usize {
    let key = u32::from(data[at]) | u32::from(data[at + 1]) << 8 | u32::from(data[at + 2]) << 16;
    (key.wrapping_mul(0x9E37_79B1) >> (32 - HASH_BITS)) as usize
}

/// Greedy parse: a run of 4+ equal bytes, else the longest (then nearest) back-reference, else literals.
fn encode_stream(data: &[u8]) -> Vec<u8> {
    let n = data.len();
    // prev[q] is the nearest earlier position with the same hash; a stop is a position where a token can start.
    let mut head = vec![u32::MAX; 1 << HASH_BITS];
    let mut prev = vec![u32::MAX; n];
    let mut stop = vec![false; n];
    for q in 0..n.saturating_sub(2) {
        let bucket = hash3(data, q);
        let mut source = head[bucket];
        while source != u32::MAX && q - source as usize <= WINDOW {
            if data[source as usize..source as usize + 3] == data[q..q + 3] {
                stop[q] = true;
                break;
            }
            source = prev[source as usize];
        }
        prev[q] = head[bucket];
        head[bucket] = q as u32;
    }
    let mut start = 0;
    while start < n {
        let mut end = start + 1;
        while end < n && data[end] == data[start] {
            end += 1;
        }
        if end - start >= 4 {
            stop[start] = true;
        }
        start = end;
    }

    let mut out = Vec::with_capacity(n / 2 + 16);
    let mut pos = 0;
    while pos < n {
        if stop[pos] {
            let mut repeat = 1;
            if pos + 4 <= n && data[pos + 1..pos + 4].iter().all(|&b| b == data[pos]) {
                let cap = (pos + MAX_REPEAT).min(n);
                repeat = data[pos..cap].iter().take_while(|&&b| b == data[pos]).count();
            }
            let (mut length, mut distance) = (0, 0);
            let limit = MAX_MATCH.min(n - pos);
            let mut source = prev[pos];
            while source != u32::MAX && pos - source as usize <= WINDOW {
                let s = source as usize;
                let mut k = 0;
                while k < limit && data[s + k] == data[pos + k] {
                    k += 1;
                }
                if k > length {
                    length = k;
                    distance = pos - s;
                    if k == limit {
                        break;
                    }
                }
                source = prev[s];
            }
            if length < 3 {
                length = 0;
            }
            if repeat >= 4 && (repeat > length || repeat > MAX_MATCH) {
                let coded = repeat - 4;
                out.extend([0x40 | (coded >> 8) as u8, coded as u8, data[pos]]);
                pos += repeat;
                continue;
            }
            if length >= 3 {
                let coded = distance - 1;
                out.extend([0x80 | ((length - 3) << 3) as u8 | (coded >> 8) as u8, coded as u8]);
                pos += length;
                continue;
            }
        }
        let begin = pos;
        let cap = n.min(begin + MAX_LITERAL);
        pos += 1;
        while pos < cap && !stop[pos] {
            pos += 1;
        }
        out.push((pos - begin) as u8);
        out.extend_from_slice(&data[begin..pos]);
    }
    out.push(0);
    while out.len() % 16 != 0 {
        out.push(0);
    }
    out
}

#[pyfunction]
fn lzp2_encode_stream<'py>(py: Python<'py>, data: &[u8]) -> Bound<'py, PyBytes> {
    let stream = py.detach(|| encode_stream(data));
    PyBytes::new(py, &stream)
}

fn decode_stream(stream: &[u8], expected: usize) -> Result<(Vec<u8>, usize), &'static str> {
    let mut out: Vec<u8> = Vec::with_capacity(expected.min(1 << 28));
    let mut pos = 0;
    while pos < stream.len() {
        let token = stream[pos];
        pos += 1;
        if token == 0 {
            if stream[pos..].iter().any(|&b| b != 0) {
                return Err("LZP2 stream padding is not zero");
            }
            if out.len() != expected {
                return Err("LZP2 output size does not match its declared size");
            }
            return Ok((out, pos));
        } else if token < 0x40 {
            let end = pos + token as usize;
            if end > stream.len() {
                return Err("LZP2 literal run is truncated");
            }
            out.extend_from_slice(&stream[pos..end]);
            pos = end;
        } else if token < 0x80 {
            if pos + 2 > stream.len() {
                return Err("LZP2 repeated-byte run is truncated");
            }
            let run = (usize::from(token & 0x3F) << 8 | usize::from(stream[pos])) + 4;
            out.resize(out.len() + run, stream[pos + 1]);
            pos += 2;
        } else {
            if pos >= stream.len() {
                return Err("LZP2 back-reference is truncated");
            }
            let run = usize::from((token >> 3) & 0x0F) + 3;
            let distance = (usize::from(token & 7) << 8 | usize::from(stream[pos])) + 1;
            pos += 1;
            if distance > out.len() {
                return Err("LZP2 back-reference exceeds output");
            }
            let start = out.len() - distance;
            for k in 0..run {
                out.push(out[start + k]);
            }
        }
        if out.len() > expected {
            return Err("LZP2 output exceeds its declared size");
        }
    }
    Err("LZP2 stream has no end token")
}

#[pyfunction]
fn lzp2_decode_stream<'py>(
    py: Python<'py>,
    stream: &[u8],
    expected_size: usize,
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    let (out, used) = py
        .detach(|| decode_stream(stream, expected_size))
        .map_err(PyValueError::new_err)?;
    Ok((PyBytes::new(py, &out), used))
}

/// RGB565 of a colour, rounded as the Python encoder does.
fn to565(c: [i32; 3]) -> i32 {
    ((c[0] * 31 + 127) / 255) << 11 | ((c[1] * 63 + 127) / 255) << 5 | (c[2] * 31 + 127) / 255
}

fn from565(v: i32) -> [i32; 3] {
    let (r, g, b) = ((v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F);
    [r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2]
}

type Pixel = [i32; 4];

/// The endpoints from the darkest and brightest pixel (by channel sum, then red, green, blue).
fn endpoints(pixels: &[Pixel; 16], visible: i32) -> (i32, i32) {
    let any = pixels.iter().any(|p| p[3] >= visible);
    let key = |p: &Pixel| (p[0] + p[1] + p[2], p[0], p[1], p[2]);
    let chosen = pixels.iter().filter(|p| !any || p[3] >= visible);
    let low = chosen.clone().map(key).min().unwrap();
    let high = chosen.map(key).max().unwrap();
    let (a, b) = (to565([low.1, low.2, low.3]), to565([high.1, high.2, high.3]));
    let (mut c0, mut c1) = (a.max(b), a.min(b));
    if c0 == c1 {
        if c0 < 0xFFFF {
            c0 += 1;
        } else {
            c1 -= 1;
        }
    }
    (c0, c1)
}

/// The four-colour palette; both mixes round down.
fn palette(c0: i32, c1: i32) -> [[i32; 3]; 4] {
    let (a, b) = (from565(c0), from565(c1));
    let mix = |l: i32, r: i32| -> [i32; 3] { std::array::from_fn(|i| (a[i] * l + b[i] * r) / 3) };
    [a, b, mix(2, 1), mix(1, 2)]
}

/// The nearest palette colour's index and squared distance, the lowest index on a tie.
fn nearest(p: &Pixel, pal: &[[i32; 3]; 4]) -> (u32, i32) {
    let mut best = (0, i32::MAX);
    for (i, c) in pal.iter().enumerate() {
        let d: i32 = (0..3).map(|k| (p[k] - c[k]) * (p[k] - c[k])).sum();
        if d < best.1 {
            best = (i as u32, d);
        }
    }
    best
}

/// Squared colour error weighted by squared alpha.
fn visible_error(pixels: &[Pixel; 16], (c0, c1): (i32, i32)) -> i64 {
    let pal = palette(c0, c1);
    pixels
        .iter()
        .filter(|p| p[3] != 0)
        .map(|p| i64::from(p[3] * p[3]) * i64::from(nearest(p, &pal).1))
        .sum()
}

fn encode_block(pixels: &[Pixel; 16], out: &mut Vec<u8>) {
    // alpha: eight levels, or six plus 0 and 255 when both ends are equal
    let a0 = pixels.iter().map(|p| p[3]).max().unwrap();
    let a1 = pixels.iter().map(|p| p[3]).min().unwrap();
    let mut levels = [a0, a1, 0, 0, 0, 0, 0, 0];
    if a0 > a1 {
        for i in 1..7 {
            levels[1 + i] = ((7 - i as i32) * a0 + i as i32 * a1) / 7;
        }
    } else {
        for i in 1..5 {
            levels[1 + i] = ((5 - i as i32) * a0 + i as i32 * a1) / 5;
        }
        levels[6] = 0;
        levels[7] = 255;
    }
    let mut alpha_bits: u64 = 0;
    for (n, p) in pixels.iter().enumerate() {
        let mut best = (0u64, i32::MAX);
        for (i, v) in levels.iter().enumerate() {
            let d = (p[3] - v).abs();
            if d < best.1 {
                best = (i as u64, d);
            }
        }
        alpha_bits |= best.0 << (n * 3);
    }
    out.push(a0 as u8);
    out.push(a1 as u8);
    out.extend_from_slice(&alpha_bits.to_le_bytes()[..6]);

    let mut ends = endpoints(pixels, 128);
    let lit = endpoints(pixels, 1);
    if lit != ends && visible_error(pixels, lit) < visible_error(pixels, ends) {
        ends = lit;
    }
    let pal = palette(ends.0, ends.1);
    let mut indices = 0u32;
    for (n, p) in pixels.iter().enumerate() {
        indices |= nearest(p, &pal).0 << (n * 2);
    }
    out.extend_from_slice(&(ends.0 as u16).to_le_bytes());
    out.extend_from_slice(&(ends.1 as u16).to_le_bytes());
    out.extend_from_slice(&indices.to_le_bytes());
}

fn encode_dxt5_blocks(rgba: &[u8], width: usize, height: usize) -> Vec<u8> {
    let (bw, bh) = (width.div_ceil(4).max(1), height.div_ceil(4).max(1));
    let mut out = Vec::with_capacity(bw * bh * 16);
    let mut last: Option<([u8; 64], usize)> = None;
    for by in 0..bh {
        for bx in 0..bw {
            let mut raw = [0u8; 64];
            for ly in 0..4 {
                let y = (by * 4 + ly).min(height - 1);
                for lx in 0..4 {
                    let x = (bx * 4 + lx).min(width - 1);
                    let at = (y * width + x) * 4;
                    raw[(ly * 4 + lx) * 4..][..4].copy_from_slice(&rgba[at..at + 4]);
                }
            }
            // flat art repeats blocks: reuse the previous block's bytes
            if let Some((prev_raw, at)) = &last {
                if *prev_raw == raw {
                    let copy: [u8; 16] = out[*at..*at + 16].try_into().unwrap();
                    out.extend_from_slice(&copy);
                    continue;
                }
            }
            let pixels: [Pixel; 16] = std::array::from_fn(|i| {
                std::array::from_fn(|k| i32::from(raw[i * 4 + k]))
            });
            let at = out.len();
            encode_block(&pixels, &mut out);
            last = Some((raw, at));
        }
    }
    out
}

#[pyfunction]
fn dxt5_encode<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    if width == 0 || height == 0 || rgba.len() != width * height * 4 {
        return Err(PyValueError::new_err("RGBA size does not match the texture geometry"));
    }
    let out = py.detach(|| encode_dxt5_blocks(rgba, width, height));
    Ok(PyBytes::new(py, &out))
}

#[pymodule]
fn wozmod_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(lzp2_encode_stream, module)?)?;
    module.add_function(wrap_pyfunction!(lzp2_decode_stream, module)?)?;
    module.add_function(wrap_pyfunction!(dxt5_encode, module)?)?;
    Ok(())
}
