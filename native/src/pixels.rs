//! The per-pixel loops of reading the donor lettering faces (wozmod.art.faces), off Python's interpreter.
//!
//! Each function gives what the Python loop it replaced gave, so the faces and the art do not change: images are
//! RGBA bytes, row by row, and a coordinate is (x, y). tests/test_art_pixels.py holds the old loops' answers on
//! small images. The loops let go of the GIL, so the faces are read on threads side by side.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::{HashSet, VecDeque};

const NONE: u32 = u32::MAX;

fn check(data: &[u8], width: usize, height: usize, channels: usize, what: &str) -> PyResult<()> {
    if data.len() != width * height * channels {
        return Err(PyValueError::new_err(format!(
            "{what}: {} bytes is not {width}x{height} pixels of {channels} bytes",
            data.len()
        )));
    }
    Ok(())
}

fn rgba_word(p: &[u8]) -> u32 {
    u32::from_le_bytes([p[0], p[1], p[2], p[3]])
}

/// The 4-connected strokes of the pixels whose colour is one of `picked`, each of at least `least` pixels, ordered
/// by their leftmost pixel (the topmost of that column first, when two start in one column). Points are (x, y).
fn find_strokes(rgba: &[u8], width: usize, height: usize, picked: &HashSet<u32>, least: usize) -> Vec<Vec<(usize, usize)>> {
    let mut fill: Vec<bool> = rgba.chunks_exact(4).map(|p| picked.contains(&rgba_word(p))).collect();
    let mut found: Vec<((usize, usize), Vec<(usize, usize)>)> = Vec::new();
    for start in 0..width * height {
        if !fill[start] {
            continue;
        }
        fill[start] = false;
        let (mut stack, mut stroke) = (vec![start], Vec::new());
        let mut first = (usize::MAX, usize::MAX);
        while let Some(at) = stack.pop() {
            let (x, y) = (at % width, at / width);
            stroke.push((x, y));
            first = first.min((x, y));
            let mut visit = |near: usize| {
                if fill[near] {
                    fill[near] = false;
                    stack.push(near);
                }
            };
            if x + 1 < width {
                visit(at + 1);
            }
            if x > 0 {
                visit(at - 1);
            }
            if y + 1 < height {
                visit(at + width);
            }
            if y > 0 {
                visit(at - width);
            }
        }
        if stroke.len() >= least {
            found.push((first, stroke));
        }
    }
    found.sort_by_key(|(first, _)| *first);
    found.into_iter().map(|(_, stroke)| stroke).collect()
}

#[pyfunction]
pub fn strokes(
    py: Python<'_>,
    rgba: &[u8],
    width: usize,
    height: usize,
    picked: Vec<(u8, u8, u8, u8)>,
    least: usize,
) -> PyResult<Vec<Vec<(usize, usize)>>> {
    check(rgba, width, height, 4, "strokes")?;
    let picked: HashSet<u32> = picked.into_iter().map(|(r, g, b, a)| u32::from_le_bytes([r, g, b, a])).collect();
    Ok(py.detach(|| find_strokes(rgba, width, height, &picked, least)))
}

/// Each pixel's fill: a fill's own points are its own, and every other pixel with any alpha that a chain of such
/// pixels (8-connected) joins to a fill is the fill's whose points the fewest steps lead from, the first fill on a
/// tie. Returns each fill's box (x0, y0, x1, y1, exclusive) and the image's pixels in it that are the fill's, the
/// others blank.
fn find_cuts(rgba: &[u8], width: usize, height: usize, fills: &[Vec<(usize, usize)>]) -> Vec<(usize, usize, usize, usize, Vec<u8>)> {
    let mut label = vec![NONE; width * height];
    let mut queue = VecDeque::new();
    for (k, fill) in fills.iter().enumerate() {
        for &(x, y) in fill {
            label[y * width + x] = k as u32;
            queue.push_back((x, y));
        }
    }
    while let Some((x, y)) = queue.pop_front() {
        let own = label[y * width + x];
        for i in -1i64..=1 {
            for j in -1i64..=1 {
                let (nx, ny) = (x as i64 + i, y as i64 + j);
                if nx < 0 || ny < 0 || nx >= width as i64 || ny >= height as i64 {
                    continue;
                }
                let at = ny as usize * width + nx as usize;
                if label[at] == NONE && rgba[4 * at + 3] != 0 {
                    label[at] = own;
                    queue.push_back((nx as usize, ny as usize));
                }
            }
        }
    }
    let mut boxes = vec![(usize::MAX, usize::MAX, 0usize, 0usize); fills.len()];
    for (at, &k) in label.iter().enumerate() {
        if k != NONE {
            let (x, y, b) = (at % width, at / width, &mut boxes[k as usize]);
            *b = (b.0.min(x), b.1.min(y), b.2.max(x + 1), b.3.max(y + 1));
        }
    }
    boxes
        .into_iter()
        .enumerate()
        .map(|(k, (x0, y0, x1, y1))| {
            if x0 > x1 {
                return (0, 0, 0, 0, Vec::new());
            }
            let w = x1 - x0;
            let mut cut = vec![0u8; w * (y1 - y0) * 4];
            for y in y0..y1 {
                for x in x0..x1 {
                    let at = y * width + x;
                    if label[at] == k as u32 {
                        let to = ((y - y0) * w + (x - x0)) * 4;
                        cut[to..to + 4].copy_from_slice(&rgba[4 * at..4 * at + 4]);
                    }
                }
            }
            (x0, y0, x1, y1, cut)
        })
        .collect()
}

#[pyfunction]
pub fn nearest_cuts<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    fills: Vec<Vec<(usize, usize)>>,
) -> PyResult<Vec<(usize, usize, usize, usize, Bound<'py, PyBytes>)>> {
    check(rgba, width, height, 4, "nearest_cuts")?;
    if fills.iter().flatten().any(|&(x, y)| x >= width || y >= height) {
        return Err(PyValueError::new_err("nearest_cuts: a fill point is outside the image"));
    }
    let cuts = py.detach(|| find_cuts(rgba, width, height, &fills));
    Ok(cuts.into_iter().map(|(x0, y0, x1, y1, cut)| (x0, y0, x1, y1, PyBytes::new(py, &cut))).collect())
}

/// The image with its glow taken out (wozmod.art.imaging.unglowed): a pixel whose alpha is 200 or less loses the
/// halo's share of its alpha where an opaque pixel (alpha over 200) lies within 3 pixels each way and the halo is
/// below 255, rounded half to even as Python's round() is, and all of it elsewhere. Opaque pixels stay as they are.
fn unglow(rgba: &[u8], halo: &[u8], width: usize, height: usize) -> Vec<u8> {
    let opaque: Vec<bool> = rgba.chunks_exact(4).map(|p| p[3] > 200).collect();
    // the 7x7 window's maximum, each axis on its own; the window stops at the edges (Pillow's MaxFilter(7))
    let mut rows = vec![false; width * height];
    for y in 0..height {
        for x in 0..width {
            rows[y * width + x] = (x.saturating_sub(3)..(x + 4).min(width)).any(|i| opaque[y * width + i]);
        }
    }
    let mut out = rgba.to_vec();
    for y in 0..height {
        for x in 0..width {
            let at = y * width + x;
            let a = rgba[4 * at + 3];
            if a > 200 {
                continue;
            }
            let near = (y.saturating_sub(3)..(y + 4).min(height)).any(|j| rows[j * width + x]);
            let h = i32::from(halo[at]);
            out[4 * at + 3] = if near && h < 255 {
                let left = (i32::from(a) - h).max(0);
                (f64::from(left * 255) / f64::from(255 - h)).round_ties_even() as u8
            } else {
                0
            };
        }
    }
    out
}

#[pyfunction]
pub fn unglowed<'py>(py: Python<'py>, rgba: &[u8], halo: &[u8], width: usize, height: usize) -> PyResult<Bound<'py, PyBytes>> {
    check(rgba, width, height, 4, "unglowed image")?;
    check(halo, width, height, 1, "unglowed halo")?;
    let out = py.detach(|| unglow(rgba, halo, width, height));
    Ok(PyBytes::new(py, &out))
}
