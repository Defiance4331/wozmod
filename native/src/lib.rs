//! Fast LZP2 and DXT5 codecs. Each function gives the same bytes as the Python codec in wozmod.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::HashMap;

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

/// The palette a block with these endpoints shows: four colours, or three before the transparent one.
fn palette(c0: i32, c1: i32, transparent: bool) -> Vec<[i32; 3]> {
    let (a, b) = (from565(c0), from565(c1));
    let mix = |l: i32, r: i32, d: i32| -> [i32; 3] { std::array::from_fn(|i| (a[i] * l + b[i] * r) / d) };
    if transparent {
        vec![a, b, mix(1, 1, 2)]
    } else {
        vec![a, b, mix(2, 1, 3), mix(1, 2, 3)]
    }
}

fn distance(p: &[i32; 3], c: &[i32; 3]) -> i64 {
    (0..3).map(|k| i64::from((p[k] - c[k]) * (p[k] - c[k]))).sum()
}

/// The nearest palette colour's index and squared distance, the lowest index on a tie.
fn nearest(p: &[i32; 3], pal: &[[i32; 3]]) -> (u32, i64) {
    let mut best = (0, i64::MAX);
    for (i, c) in pal.iter().enumerate() {
        let d = distance(p, c);
        if d < best.1 {
            best = (i as u32, d);
        }
    }
    best
}

/// Squared colour error weighted by squared alpha.
fn visible_error(pixels: &[Pixel; 16], (c0, c1): (i32, i32)) -> i64 {
    let pal = palette(c0, c1, false);
    pixels
        .iter()
        .filter(|p| p[3] != 0)
        .map(|p| i64::from(p[3] * p[3]) * nearest(&[p[0], p[1], p[2]], &pal).1)
        .sum()
}

/// A colour and what an error in it costs (squared alpha).
type Weighted = ([i32; 3], i64);

fn color_error(colors: &[Weighted], c0: i32, c1: i32) -> i64 {
    let pal = palette(c0, c1, false);
    colors.iter().map(|(rgb, w)| w * nearest(rgb, &pal).1).sum()
}

fn rounded_quotient(numerator: i128, denominator: i128) -> i128 {
    (2 * numerator + denominator).div_euclid(2 * denominator)
}

/// Endpoints that best fit the pixels each palette index claims now; None when they cannot move.
fn least_squares_endpoints(colors: &[Weighted], c0: i32, c1: i32) -> Option<(i32, i32)> {
    const SHARES: [(i128, i128); 4] = [(3, 0), (0, 3), (2, 1), (1, 2)];
    let pal = palette(c0, c1, false);
    let (mut aa, mut ab, mut bb) = (0i128, 0i128, 0i128);
    let (mut xa, mut xb) = ([0i128; 3], [0i128; 3]);
    for (rgb, w) in colors {
        let (first, second) = SHARES[nearest(rgb, &pal).0 as usize];
        let w = i128::from(*w);
        aa += w * first * first;
        ab += w * first * second;
        bb += w * second * second;
        for k in 0..3 {
            xa[k] += w * first * i128::from(rgb[k]);
            xb[k] += w * second * i128::from(rgb[k]);
        }
    }
    let determinant = aa * bb - ab * ab;
    if determinant == 0 {
        return None;
    }
    let end = |near: &[i128; 3], far: &[i128; 3], own: i128| -> i32 {
        to565(std::array::from_fn(|k| {
            rounded_quotient(3 * (near[k] * own - far[k] * ab), determinant).clamp(0, 255) as i32
        }))
    };
    Some((end(&xa, &xb, bb), end(&xb, &xa, aa)))
}

/// Every endpoint pair one step of one channel away, in a fixed order.
fn color_neighbours(c0: i32, c1: i32) -> Vec<(i32, i32)> {
    let mut out = Vec::with_capacity(12);
    for which in 0..2 {
        let value = if which == 0 { c0 } else { c1 };
        for (shift, limit) in [(11, 31), (5, 63), (0, 31)] {
            let field = value >> shift & limit;
            for delta in [-1, 1] {
                if (0..=limit).contains(&(field + delta)) {
                    let moved = value + (delta << shift);
                    out.push(if which == 0 { (moved, c1) } else { (c0, moved) });
                }
            }
        }
    }
    out
}

/// Endpoints with the least weighted error: fit by least squares, then step to better neighbours.
fn search_color(colors: &[Weighted], starts: &[(i32, i32)]) -> (i32, i32) {
    let mut best: Option<(i64, (i32, i32))> = None;
    for &start in starts {
        let (mut ends, mut error) = (start, color_error(colors, start.0, start.1));
        for _ in 0..64 {
            let mut changed = false;
            if let Some(mut fitted) = least_squares_endpoints(colors, ends.0, ends.1) {
                if fitted.0 <= fitted.1 {
                    fitted = (fitted.1, fitted.0);
                }
                if fitted.0 > fitted.1 {
                    let e = color_error(colors, fitted.0, fitted.1);
                    if e < error {
                        (ends, error, changed) = (fitted, e, true);
                    }
                }
            }
            let mut step: Option<(i64, (i32, i32))> = None;
            for n in color_neighbours(ends.0, ends.1) {
                if n.0 > n.1 {
                    let e = color_error(colors, n.0, n.1);
                    if e < error && step.is_none_or(|(b, _)| e < b) {
                        step = Some((e, n));
                    }
                }
            }
            if let Some((e, n)) = step {
                (ends, error, changed) = (n, e, true);
            }
            if !changed || error == 0 {
                break;
            }
        }
        if best.is_none_or(|(b, _)| error < b) {
            best = Some((error, ends));
        }
    }
    best.unwrap().1
}

fn alpha_levels(a0: i32, a1: i32) -> [i32; 8] {
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
    levels
}

fn nearest_level(value: i32, levels: &[i32; 8]) -> (usize, i32) {
    let mut best = (0, i32::MAX);
    for (i, v) in levels.iter().enumerate() {
        let d = (value - v).abs();
        if d < best.1 {
            best = (i, d);
        }
    }
    best
}

fn alpha_error(counts: &[(i32, i32)], a0: i32, a1: i32) -> i64 {
    let levels = alpha_levels(a0, a1);
    counts
        .iter()
        .map(|&(v, n)| i64::from(n) * i64::from(nearest_level(v, &levels).1).pow(2))
        .sum()
}

const ALPHA_SIZES: [i32; 5] = [16, 8, 4, 2, 1];
const ALPHA_GRID: [i32; 5] = [0, 64, 128, 192, 255];

/// Alpha endpoints with the least squared error. Two or fewer alphas are exact at (max, min). Otherwise each
/// start (the extremes in either interpolation mode, and a coarse grid of pairs) moves by steps of 16, 8, 4, 2
/// and 1 to its best neighbour, until none is better.
fn search_alpha(counts: &[(i32, i32)]) -> (i32, i32) {
    let (low, high) = (counts[0].0, counts[counts.len() - 1].0);
    if counts.len() <= 2 {
        return (high, low);
    }
    let mut known: HashMap<(i32, i32), i64> = HashMap::new();
    let mut error = |ends: (i32, i32)| -> i64 {
        *known.entry(ends).or_insert_with(|| alpha_error(counts, ends.0, ends.1))
    };
    let starts = [(high, low), (low, high)]
        .into_iter()
        .chain(ALPHA_GRID.iter().flat_map(|&x| ALPHA_GRID.iter().map(move |&y| (x, y))));
    let mut best: Option<(i64, (i32, i32))> = None;
    for mut ends in starts {
        for size in ALPHA_SIZES {
            let moves = [(-size, 0), (size, 0), (0, -size), (0, size), (-size, -size), (size, size), (-size, size), (size, -size)];
            loop {
                let here = error(ends);
                let mut step: Option<(i64, (i32, i32))> = None;
                for (d0, d1) in moves {
                    let n = (ends.0 + d0, ends.1 + d1);
                    if (0..=255).contains(&n.0) && (0..=255).contains(&n.1) {
                        let e = error(n);
                        if e < here && step.is_none_or(|(b, _)| e < b) {
                            step = Some((e, n));
                        }
                    }
                }
                match step {
                    Some((_, n)) => ends = n,
                    None => break,
                }
            }
        }
        let e = error(ends);
        if best.is_none_or(|(b, _)| e < b) {
            best = Some((e, ends));
        }
    }
    best.unwrap().1
}

type AlphaCache = HashMap<Vec<(i32, i32)>, (i32, i32)>;

fn encode_block(pixels: &[Pixel; 16], out: &mut Vec<u8>, cache: &mut AlphaCache) {
    let mut counts: Vec<(i32, i32)> = Vec::with_capacity(16);
    for p in pixels {
        match counts.iter_mut().find(|(v, _)| *v == p[3]) {
            Some(entry) => entry.1 += 1,
            None => counts.push((p[3], 1)),
        }
    }
    counts.sort_unstable();
    let (a0, a1) = *cache.entry(counts.clone()).or_insert_with(|| search_alpha(&counts));
    let levels = alpha_levels(a0, a1);
    let mut alpha_bits: u64 = 0;
    for (n, p) in pixels.iter().enumerate() {
        alpha_bits |= (nearest_level(p[3], &levels).0 as u64) << (n * 3);
    }
    out.push(a0 as u8);
    out.push(a1 as u8);
    out.extend_from_slice(&alpha_bits.to_le_bytes()[..6]);

    let mut ends = endpoints(pixels, 128);
    let lit = endpoints(pixels, 1);
    if lit != ends && visible_error(pixels, lit) < visible_error(pixels, ends) {
        ends = lit;
    }
    let mut colors: Vec<Weighted> = Vec::with_capacity(16);
    for p in pixels {
        let weight = i64::from(p[3] * p[3]);
        if weight != 0 {
            let rgb = [p[0], p[1], p[2]];
            match colors.iter_mut().find(|(c, _)| *c == rgb) {
                Some(entry) => entry.1 += weight,
                None => colors.push((rgb, weight)),
            }
        }
    }
    if !colors.is_empty() {
        let low: [i32; 3] = std::array::from_fn(|k| colors.iter().map(|(c, _)| c[k]).min().unwrap());
        let high: [i32; 3] = std::array::from_fn(|k| colors.iter().map(|(c, _)| c[k]).max().unwrap());
        let (a, b) = (to565(low), to565(high));
        let mut starts = vec![ends];
        if a != b {
            starts.push((a.max(b), a.min(b)));
        }
        ends = search_color(&colors, &starts);
    }
    let pal = palette(ends.0, ends.1, false);
    let mut indices = 0u32;
    for (n, p) in pixels.iter().enumerate() {
        indices |= nearest(&[p[0], p[1], p[2]], &pal).0 << (n * 2);
    }
    out.extend_from_slice(&(ends.0 as u16).to_le_bytes());
    out.extend_from_slice(&(ends.1 as u16).to_le_bytes());
    out.extend_from_slice(&indices.to_le_bytes());
}

/// The pixels a base texture shows, and the compressed blocks that show them.
struct Base<'a> {
    decoded: &'a [u8],
    payload: &'a [u8],
    width: usize,
    height: usize,
}

fn encode_dxt5_blocks(rgba: &[u8], width: usize, height: usize, base: Option<Base>) -> Vec<u8> {
    let (bw, bh) = (width.div_ceil(4).max(1), height.div_ceil(4).max(1));
    let mut out = Vec::with_capacity(bw * bh * 16);
    let mut last: Option<([u8; 64], usize)> = None;
    let mut cache = AlphaCache::new();
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
            // a block the base already shows these pixels in keeps the base's bytes (edge blocks repeat pixels: never)
            if let Some(b) = &base {
                if bx < b.width / 4 && by < b.height / 4 {
                    let same = (0..4).all(|ly| {
                        let at = ((by * 4 + ly) * b.width + bx * 4) * 4;
                        b.decoded[at..at + 16] == raw[ly * 16..ly * 16 + 16]
                    });
                    if same {
                        let at = (by * b.width.div_ceil(4) + bx) * 16;
                        out.extend_from_slice(&b.payload[at..at + 16]);
                        continue;
                    }
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
            if cache.len() > 1 << 16 {
                cache.clear();
            }
            encode_block(&pixels, &mut out, &mut cache);
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
    let out = py.detach(|| encode_dxt5_blocks(rgba, width, height, None));
    Ok(PyBytes::new(py, &out))
}

/// Like dxt5_encode, but a block that the base (its decoded RGBA and DXT5 payload) shows the same pixels in keeps its bytes.
#[pyfunction]
#[pyo3(signature = (rgba, width, height, base_decoded, base_payload, base_width, base_height))]
fn dxt5_encode_edit<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    base_decoded: &[u8],
    base_payload: &[u8],
    base_width: usize,
    base_height: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let blocks = base_width.div_ceil(4).max(1) * base_height.div_ceil(4).max(1);
    if width == 0 || height == 0 || rgba.len() != width * height * 4 {
        return Err(PyValueError::new_err("RGBA size does not match the texture geometry"));
    }
    if base_decoded.len() != base_width * base_height * 4 || base_payload.len() < blocks * 16 {
        return Err(PyValueError::new_err("base texture does not match its geometry"));
    }
    let base = Base { decoded: base_decoded, payload: base_payload, width: base_width, height: base_height };
    let out = py.detach(|| encode_dxt5_blocks(rgba, width, height, Some(base)));
    Ok(PyBytes::new(py, &out))
}

#[pymodule]
fn wozmod_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(lzp2_encode_stream, module)?)?;
    module.add_function(wrap_pyfunction!(lzp2_decode_stream, module)?)?;
    module.add_function(wrap_pyfunction!(dxt5_encode, module)?)?;
    module.add_function(wrap_pyfunction!(dxt5_encode_edit, module)?)?;
    Ok(())
}
