//! The Dream voice separator's network as a GPU runs it: its tensors and, in order, the steps that turn a chunk's
//! spectrum into masks. Both GPU engines (cuda.rs, vulkan.rs) run this one description, each with its own kernels
//! for the six kinds of step, so the two cannot come to compute different networks.
//!
//! The weights are the CPU graph's: separator.weights, its 4-bit matrices expanded to 16-bit floats and laid out
//! [outputs][inputs]. Activations are 16-bit float rows, the token of frame t and band b in row t * 60 + b. A chunk
//! of `frames` frames runs as:
//!
//! 1. The band split: each band's features, normalised, then through its own matrix (one product a band).
//! 2. Twelve transformers (six layers of one over time, then one over the bands), each: a norm; the products for
//!    the queries, keys and values, the rotary turn in their last step; the gates; the attention; the product back
//!    to 384 wide, added to the input; a norm; the two products of the feed-forward, the first with GELU, the
//!    second added to the input; a norm.
//! 3. The 60 mask estimators (three products a band, tanh after the first two) and the gated half of their last.
//!
//! No tensor is larger than `limit` bytes, the most a device lets one buffer binding hold (Vulkan's
//! maxStorageBufferRange; 128 MB is the least a driver may report): the one activation that can be, the
//! feed-forward's 1536-wide middle, is cut into slabs of rows, each with its own products.

use std::collections::HashMap;

use crate::separator::{stored_values, Stored, ROWS};

pub const BANDS: usize = 60;
pub const DIM: usize = 384;
pub const WIDE: usize = 1536; // the feed-forward's middle, and the mask estimators'
pub const HEADS: usize = 512; // 8 heads of 64
pub const EPSILON: f32 = 1e-24;

/// A tensor's place in the list of tensors.
pub type Id = usize;

/// What a place in device memory holds: `bytes` of it, starting as `data` where it is a weight or a table. `slack`
/// more bytes follow it, which a kernel that reads whole tiles may read (never use) past the tensor's end.
pub struct Tensor {
    pub bytes: usize,
    pub data: Option<Vec<u8>>,
    pub slack: usize,
}

/// A place inside a tensor, in the elements its reader counts in: 16-bit floats, or 32-bit floats or integers.
#[derive(Clone, Copy)]
pub struct At {
    pub tensor: Id,
    pub offset: usize,
}

/// The rotary turn of a product's first `columns` columns: row m is at position (m / pdiv) % pmod of its sequence.
#[derive(Clone, Copy)]
pub struct Rotary {
    pub cos: At,
    pub sin: At,
    pub pdiv: usize,
    pub pmod: usize,
    pub columns: usize,
}

/// One matrix product: c[m][n] = act(sum over k of a[m][k] * b[n][k] + bias[n]) + r[m][n], then the rotary turn.
#[derive(Clone, Copy)]
pub struct Product {
    pub a: At,
    pub b: At,
    pub bias: Option<At>,
    pub r: Option<At>,
    pub c: At,
    pub m: usize,
    pub n: usize,
    pub k: usize, // a multiple of 32
    pub lda: usize,
    pub ldb: usize,
    pub ldc: usize,
    pub ldr: usize,
    pub act: u32, // 0 none, 1 GELU, 2 tanh
    pub rotary: Option<Rotary>,
}

pub enum Step {
    /// Each band's features of each frame, normalised, zero-padded to `padded` wide: features[band][frame][..].
    BandSplit { spec: At, picked: At, edges: At, gammas: At, features: At, padded: usize },
    /// Products that run side by side.
    Products(Vec<Product>),
    /// to = x / rms(x) * gamma, over rows of 384.
    Norm { x: At, gamma: At, to: At },
    /// to[row][head] = sigmoid(normed[row] . weights[head] + bias[head]), as 32-bit floats.
    Gates { normed: At, weights: At, bias: At, to: At },
    /// to = softmax(q k^T / 8) v per head, times the row's gate. `sequences` sequences of `length` positions; a
    /// sequence's rows are `step` apart and the next sequence starts `between` rows on.
    Attention { q: At, k: At, v: At, to: At, gates: At, length: usize, sequences: usize, between: usize, step: usize },
    /// masks[frame][offsets[band] + j] = hidden[j] * sigmoid(hidden[widths[band] + j]) for each band's last layer,
    /// rounded to a 16-bit float and written as 32-bit.
    Glu { hidden: At, starts: At, widths: At, offsets: At, masks: At },
}

pub struct Network {
    pub tensors: Vec<Tensor>,
    pub steps: Vec<Step>,
    pub spec: Id,  // rows 2 * bin + channel, then the frame, then (real, imaginary): 32-bit floats
    pub masks: Id, // frames x width 32-bit floats, each the nearest 16-bit float's value: what the host hands on
    pub frames: usize,
    pub rows: usize,  // frames * 60
    pub width: usize, // a frame's masks: 7916
}

fn up32(n: usize) -> usize {
    n.div_ceil(32) * 32
}

fn bytes_of<T: Copy>(values: &[T]) -> Vec<u8> {
    unsafe { std::slice::from_raw_parts(values.as_ptr().cast::<u8>(), std::mem::size_of_val(values)) }.to_vec()
}

/// The tensors of separator.weights by name.
struct Weights<'a> {
    data: &'a [u8],
    index: HashMap<&'a str, &'a Stored>,
}

impl Weights<'_> {
    fn stored(&self, name: &str) -> Result<&Stored, String> {
        self.index.get(name).copied().ok_or_else(|| format!("separator.json names no tensor {name}"))
    }

    fn floats(&self, name: &str) -> Result<Vec<f32>, String> {
        stored_values(self.data, self.stored(name)?)
    }

    fn halves(&self, name: &str) -> Result<Vec<half::f16>, String> {
        Ok(self.floats(name)?.into_iter().map(half::f16::from_f32).collect())
    }

    /// A matrix stored (inputs, outputs) as 16-bit floats [outputs][inputs padded with zeros to a multiple of 32]:
    /// (the values, the outputs, the padded inputs).
    fn matrix(&self, name: &str) -> Result<(Vec<half::f16>, usize, usize), String> {
        let stored = self.stored(name)?;
        let (inputs, outputs) = (stored.2[0], stored.2[1]);
        let values = stored_values(self.data, stored)?;
        let padded = up32(inputs);
        let mut out = vec![half::f16::ZERO; outputs * padded];
        for input in 0..inputs {
            for output in 0..outputs {
                out[output * padded + input] = half::f16::from_f32(values[input * outputs + output]);
            }
        }
        Ok((out, outputs, padded))
    }
}

struct Builder {
    tensors: Vec<Tensor>,
}

impl Builder {
    fn empty(&mut self, bytes: usize) -> At {
        self.tensors.push(Tensor { bytes, data: None, slack: 0 });
        At { tensor: self.tensors.len() - 1, offset: 0 }
    }

    fn holding<T: Copy>(&mut self, values: &[T]) -> At {
        self.tensors.push(Tensor { bytes: std::mem::size_of_val(values), data: Some(bytes_of(values)), slack: 0 });
        At { tensor: self.tensors.len() - 1, offset: 0 }
    }
}

fn after(at: At, elements: usize) -> At {
    At { tensor: at.tensor, offset: at.offset + elements }
}

impl Network {
    /// The network for chunks of `frames` frames, from separator.weights (`weights`, with what separator.json says
    /// of it: `tensors` and `bands`, each band's (first bin, bins)), no tensor over `limit` bytes.
    pub fn new(weights: &[u8], tensors: &[Stored], bands: &[(usize, usize)], frames: usize, limit: usize) -> Result<Network, String> {
        let weights = Weights { data: weights, index: tensors.iter().map(|stored| (stored.0.as_str(), stored)).collect() };
        if bands.len() != BANDS {
            return Err(format!("{} bands, not {BANDS}", bands.len()));
        }
        let (t, rows) = (frames, frames * BANDS);
        let widths: Vec<usize> = bands.iter().map(|&(_, bins)| 4 * bins).collect(); // a band's features, and its masks
        let width: usize = widths.iter().sum();
        let padded = widths.iter().map(|&w| up32(w)).max().unwrap_or(32);
        let mut made = Builder { tensors: Vec::new() };
        let mut steps = Vec::new();

        // The tables: which spectrum rows the bands take, where each band starts, each band's width, and where
        // each band's last mask layer starts (in 16-bit floats).
        let picked: Vec<i32> = bands.iter().flat_map(|&(first, bins)| (first..first + bins).flat_map(|bin| [2 * bin as i32, 2 * bin as i32 + 1])).collect();
        let edges: Vec<i32> = std::iter::once(0).chain(widths.iter().scan(0, |at, w| { *at += *w as i32; Some(*at) })).collect();
        let band_widths: Vec<i32> = widths.iter().map(|&w| w as i32).collect();
        let starts: Vec<i32> = widths.iter().scan(0, |at, &w| { let start = *at; *at += (t * 2 * w) as i32; Some(start) }).collect();
        let tables = made.holding(&[picked.as_slice(), &edges, &band_widths, &starts].concat());
        let (picked_at, edges_at) = (tables, after(tables, picked.len()));
        let (widths_at, starts_at) = (after(edges_at, edges.len()), after(edges_at, edges.len() + BANDS));

        // The activations.
        let spec = made.empty(ROWS * t * 2 * 4);
        let features = made.empty(BANDS * t * padded * 2);
        let x = made.empty(rows * DIM * 2);
        let normed = made.empty(rows * DIM * 2);
        let [q, k, v] = [0; 3].map(|_| made.empty(rows * HEADS * 2));
        // An attention kernel may load its queries a whole tile of 64 positions at a time, the positions past a
        // sequence's end too (it drops what it computes for them): up to 64 positions of the widest step (60 rows).
        made.tensors[q.tensor].slack = 64 * BANDS * HEADS * 2;
        let gates = made.empty(rows * 8 * 4);
        let attended = made.empty(rows * HEADS * 2);
        // The feed-forward's middle, in slabs of rows no larger than the limit.
        let slabs = (rows * WIDE * 2).div_ceil(limit.max(1)).max(1);
        let slab_rows = rows.div_ceil(slabs);
        let hidden: Vec<(usize, usize, At)> = (0..slabs)
            .map(|s| (s * slab_rows, slab_rows.min(rows - s * slab_rows)))
            .map(|(first, count)| (first, count, made.empty(count * WIDE * 2)))
            .collect();
        let masks = made.empty(t * width * 4);

        // 1. The band split.
        let gammas: Vec<f32> = (0..BANDS).map(|b| weights.floats(&format!("band_split.to_features.{b}.0.gamma"))).collect::<Result<Vec<_>, _>>()?.concat();
        let gammas = made.holding(&gammas);
        steps.push(Step::BandSplit { spec, picked: picked_at, edges: edges_at, gammas, features, padded });
        let mut split = Vec::new();
        for b in 0..BANDS {
            let (matrix, bias) = with_bias(&weights, &mut made, &format!("band_split.to_features.{b}.1"))?;
            split.push(Product {
                a: after(features, b * t * padded), b: matrix.0, bias: Some(bias), r: None, c: after(x, b * DIM),
                m: t, n: matrix.1, k: matrix.2, lda: padded, ldb: matrix.2, ldc: BANDS * DIM, ldr: 0, act: 0, rotary: None,
            });
        }
        steps.push(Step::Products(split));

        // 2. The transformers. A row's position in its sequence: its frame over time, its band over the bands.
        let mut turns = Vec::new();
        for which in ["time", "frequency"] {
            let (cos, sin) = (weights.halves(&format!("rotary.{which}.cos"))?, weights.halves(&format!("rotary.{which}.sin"))?);
            let both = made.holding(&[cos.as_slice(), &sin].concat());
            turns.push((both, after(both, cos.len())));
        }
        for layer in 0..6 {
            for over_bands in [false, true] {
                let name = format!("layers.{layer}.{}", over_bands as usize);
                let (attention, feed) = (format!("{name}.layers.0.0"), format!("{name}.layers.0.1"));
                let gamma = |made: &mut Builder, tensor: String| -> Result<At, String> { Ok(made.holding(&weights.halves(&tensor)?)) };
                let matrix = |made: &mut Builder, tensor: String| -> Result<At, String> { Ok(made.holding(&weights.matrix(&tensor)?.0)) };
                steps.push(Step::Norm { x, gamma: gamma(&mut made, format!("{attention}.norm.gamma"))?, to: normed });
                let (cos, sin) = turns[over_bands as usize];
                let rotary = Rotary { cos, sin, pdiv: if over_bands { 1 } else { BANDS }, pmod: if over_bands { BANDS } else { 1 << 30 }, columns: HEADS };
                let mut parts = Vec::new();
                for (part, to, turned) in [("q", q, true), ("k", k, true), ("v", v, false)] {
                    parts.push(Product {
                        a: normed, b: matrix(&mut made, format!("{attention}.to_qkv.{part}"))?, bias: None, r: None, c: to,
                        m: rows, n: HEADS, k: DIM, lda: DIM, ldb: DIM, ldc: HEADS, ldr: 0, act: 0, rotary: turned.then_some(rotary),
                    });
                }
                steps.push(Step::Products(parts));
                let (gate_weights, gate_outputs, gate_inputs) = weights.matrix(&format!("{attention}.to_gates.weight"))?;
                let gate_bias = weights.halves(&format!("{attention}.to_gates.bias"))?;
                let gating = made.holding(&[gate_weights.as_slice(), &gate_bias].concat());
                steps.push(Step::Gates { normed, weights: gating, bias: after(gating, gate_outputs * gate_inputs), to: gates });
                let (length, sequences, between, step) = if over_bands { (BANDS, t, BANDS, 1) } else { (t, BANDS, 1, BANDS) };
                steps.push(Step::Attention { q, k, v, to: attended, gates, length, sequences, between, step });
                steps.push(Step::Products(vec![Product {
                    a: attended, b: matrix(&mut made, format!("{attention}.to_out.0.weight"))?, bias: None, r: Some(x), c: x,
                    m: rows, n: DIM, k: HEADS, lda: HEADS, ldb: HEADS, ldc: DIM, ldr: DIM, act: 0, rotary: None,
                }]));
                steps.push(Step::Norm { x, gamma: gamma(&mut made, format!("{feed}.net.0.gamma"))?, to: normed });
                let (wider, wider_bias) = with_bias(&weights, &mut made, &format!("{feed}.net.1"))?;
                let (back, back_bias) = with_bias(&weights, &mut made, &format!("{feed}.net.4"))?;
                steps.push(Step::Products(hidden.iter().map(|&(first, count, slab)| Product {
                    a: after(normed, first * DIM), b: wider.0, bias: Some(wider_bias), r: None, c: slab,
                    m: count, n: WIDE, k: DIM, lda: DIM, ldb: DIM, ldc: WIDE, ldr: 0, act: 1, rotary: None,
                }).collect()));
                steps.push(Step::Products(hidden.iter().map(|&(first, count, slab)| Product {
                    a: slab, b: back.0, bias: Some(back_bias), r: Some(after(x, first * DIM)), c: after(x, first * DIM),
                    m: count, n: DIM, k: WIDE, lda: WIDE, ldb: WIDE, ldc: DIM, ldr: DIM, act: 0, rotary: None,
                }).collect()));
                steps.push(Step::Norm { x, gamma: gamma(&mut made, format!("{name}.norm.gamma"))?, to: x });
            }
        }

        // 3. The mask estimators: per band 384 -> 1536 -> 1536 -> twice its width, then the gated half.
        let third = made.empty(t * 2 * width * 2);
        let mut layers: [Vec<Product>; 3] = Default::default();
        for (b, &band) in widths.iter().enumerate() {
            let layer = |made: &mut Builder, depth: usize| with_bias(&weights, made, &format!("mask_estimators.0.to_freqs.{b}.0.{}", 2 * depth));
            let (first, second) = (made.empty(t * WIDE * 2), made.empty(t * WIDE * 2));
            let product = |weight: ((At, usize, usize), At), a: At, lda: usize, c: At, act: u32| Product {
                a, b: weight.0 .0, bias: Some(weight.1), r: None, c,
                m: t, n: weight.0 .1, k: weight.0 .2, lda, ldb: weight.0 .2, ldc: weight.0 .1, ldr: 0, act, rotary: None,
            };
            layers[0].push(product(layer(&mut made, 0)?, after(x, b * DIM), BANDS * DIM, first, 2));
            layers[1].push(product(layer(&mut made, 1)?, first, WIDE, second, 2));
            let last = product(layer(&mut made, 2)?, second, WIDE, after(third, starts[b] as usize), 0);
            if last.n != 2 * band {
                return Err(format!("band {b}'s mask estimator gives {} values, not twice its {band}", last.n));
            }
            layers[2].push(last);
        }
        steps.extend(layers.into_iter().map(Step::Products));
        steps.push(Step::Glu { hidden: third, starts: starts_at, widths: widths_at, offsets: edges_at, masks });
        if let Some(over) = made.tensors.iter().find(|tensor| tensor.bytes + tensor.slack > limit) {
            return Err(format!("a tensor of {} bytes does not fit the device's {limit} bytes a buffer", over.bytes + over.slack));
        }
        Ok(Network { tensors: made.tensors, steps, spec: spec.tensor, masks: masks.tensor, frames, rows, width })
    }
}

/// A layer's matrix and bias as one tensor, the bias after the matrix: ((the matrix, its outputs, its padded
/// inputs), the bias).
fn with_bias(weights: &Weights, made: &mut Builder, name: &str) -> Result<((At, usize, usize), At), String> {
    let (matrix, outputs, inputs) = weights.matrix(&format!("{name}.weight"))?;
    let bias = weights.halves(&format!("{name}.bias"))?;
    let both = made.holding(&[matrix.as_slice(), &bias].concat());
    Ok(((both, outputs, inputs), after(both, outputs * inputs)))
}
