"""Build the Dream voice separator's model files (voices/separation.py MODEL) from the Mel-Band RoFormer checkpoint.

    uv run --group model python packaging/separator_model.py CHECKPOINT FOLDER

CHECKPOINT is KimberleyJensen's MelBandRoformer.ckpt (packaging/pins.json "separator" pins it and says where it is
published; MIT, packaging/SEPARATOR-NOTICE.txt). FOLDER gets:

- separator.weights: every weight of the network. A matrix a MatMul multiplies by is stored in 4 bits, in blocks of 64
  down each column with one scale a block (ONNX Runtime's MatMulNBits layout, symmetric); everything else is float32.
- separator.onnx: the network for the CPU, reading the 4-bit matrices as they are (MatMulNBits).
- separator.json: where each tensor is in separator.weights, and the 60 mel bands. The installer's GPU path
  (native/src/cuda.rs) runs the same network from these with its own kernels (packaging/separator_kernels.py).

The network is the one between the STFT and the mask average of lucidrains' BS-RoFormer (MIT; Kimberley Jensen's
config: 60 mel bands, 6 layers of a time and a frequency transformer, dim 384, 8 heads of 64), written with ONNX
Runtime's fused operators: MultiHeadAttention (never the 60 x 8 x 801 x 801 scores), RotaryEmbedding (interleaved
pairs), SimplifiedLayerNormalization and SkipSimplifiedLayerNormalization (RMSNorm, with the residual add) and Gelu.
F.normalize(x) * sqrt(d) * gamma is x / rms(x) * gamma, so RMSNorm is SimplifiedLayerNormalization with scale gamma.

The graph's sizes are free (spec is batch x 2050 x frames x 2). ONNX Runtime plans which buffers to hand from a dead
tensor to a later one of the same shape only when it knows the shapes, and a planned buffer stays allocated between
its uses: with fixed shapes it held 6.3 GB at the peak. With free sizes it frees each tensor at its last use: 1.7 GB.
(The setting that stops the planning, enable_mem_reuse, is not in ONNX Runtime's C interface, which the installer
uses.)

Nothing here needs PyTorch: the checkpoint is a zip of raw float32 tensors and a pickle that names them, read with an
unpickler that knows those names and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import sys
import zipfile
from collections import OrderedDict
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto as TP
from onnx import helper as h
from onnx import numpy_helper as nh

SAMPLE_RATE, N_FFT, HOP, CHUNK = 44_100, 2048, 441, 352_800  # the model's config; CHUNK is 8 s
FRAMES = CHUNK // HOP + 1  # 801 STFT frames a chunk, the most a graph takes (its rotary table's rows)
BINS = N_FFT // 2 + 1
BANDS, DIM, HEADS, HEAD = 60, 384, 8, 64
BLOCK = 64  # weights in a 4-bit block; one scale each
GROUP = 8  # inputs a Concat takes, as in the graph the separation was measured on
EPSILON = 1e-24  # under F.normalize's 1e-12 on the norm; both give 0 for an all-zero row
WEIGHTS, INDEX = "separator.weights", "separator.json"
GRAPH = "separator.onnx"


def read_checkpoint(path: Path) -> dict[str, np.ndarray]:
    """The checkpoint's tensors by name. It is torch.save's zip: data.pkl names each tensor's storage file, offset,
    shape and strides; anything else in the pickle is refused."""
    with zipfile.ZipFile(path) as archive:
        prefix = next(name for name in archive.namelist() if name.endswith("/data.pkl"))[:-len("data.pkl")]

        def tensor(storage, offset, shape, stride, *_):
            flat = np.frombuffer(archive.read(f"{prefix}data/{storage}"), "<f4")
            if tuple(stride) != tuple(int(np.prod(shape[i + 1:])) for i in range(len(shape))):
                raise ValueError("a checkpoint tensor is not stored row by row")
            return flat[offset:offset + int(np.prod(shape))].reshape(shape)

        class Reader(pickle.Unpickler):
            def find_class(self, module, name):
                known = {("collections", "OrderedDict"): OrderedDict, ("torch._utils", "_rebuild_tensor_v2"): tensor,
                         ("torch", "FloatStorage"): "float32"}
                if (module, name) not in known:
                    raise pickle.UnpicklingError(f"the checkpoint names {module}.{name}, which a tensor file does not")
                return known[module, name]

            def persistent_load(self, saved):  # ("storage", type, file, device, size)
                return saved[2]

        with archive.open(prefix + "data.pkl") as stream:
            return dict(Reader(stream).load())


def mel_bands() -> list[tuple[int, int]]:
    """(first STFT bin, bins) of each of the 60 bands: the bins where librosa's mel filter (Slaney's scale, as
    librosa.filters.mel(sr=44100, n_fft=2048, n_mels=60) computes it) is over 0, with bin 0 in the first band and
    the last bin in the last, as the model builds them."""
    def hz(mels):
        return np.where(mels >= 15.0, 1000.0 * np.exp(np.log(6.4) / 27.0 * (mels - 15.0)), 200.0 / 3 * mels)

    top = 15.0 + np.log(SAMPLE_RATE / 2 / 1000.0) / (np.log(6.4) / 27.0)
    edges = hz(np.linspace(0.0, top, BANDS + 2))
    ramps = np.subtract.outer(edges, np.fft.rfftfreq(N_FFT, 1.0 / SAMPLE_RATE))
    steps = np.diff(edges)
    found = []
    for band in range(BANDS):
        weights = np.maximum(0, np.minimum(-ramps[band] / steps[band], ramps[band + 2] / steps[band + 1]))
        weights = (weights * 2.0 / (edges[band + 2] - edges[band])).astype(np.float32)
        if band == 0:
            weights[0] = 1.0
        if band == BANDS - 1:
            weights[-1] = 1.0
        bins = np.flatnonzero(weights > 0)
        if not np.array_equal(bins, np.arange(bins[0], bins[-1] + 1)):
            raise ValueError(f"mel band {band} is not one run of bins")
        found.append((int(bins[0]), len(bins)))
    return found


def quantised(weight: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A (rows, columns) float32 matrix in 4 bits, as MatMulNBits reads it: per column, blocks of BLOCK rows (the last
    padded with zeros), each a scale (its largest value by size, over -8) and its values over the scale, rounded,
    plus 8, in 0-15 (0 for the padding); two values a byte, the first in the low half. Returns (columns x blocks x BLOCK/2 bytes,
    columns x blocks scales). It is ONNX Runtime's own quantiser (MatMulNBitsQuantizer, symmetric), to the bit."""
    rows, columns = weight.shape
    blocks = -(-rows // BLOCK)
    padded = np.zeros((blocks * BLOCK, columns), np.float32)
    padded[:rows] = weight
    cells = padded.T.reshape(columns, blocks, BLOCK)
    largest = np.take_along_axis(cells, np.abs(cells).argmax(-1)[..., None], -1)
    scales = (largest / np.float32(-8)).astype(np.float32)
    with np.errstate(divide="ignore"):
        inverse = np.where(scales != 0, np.float32(1) / scales, np.float32(0)).astype(np.float32)
    scaled = cells * inverse + np.float32(8)
    values = np.clip(np.trunc(scaled + np.copysign(np.float32(0.5), scaled)), 0, 15).astype(np.uint8)  # roundf
    values.reshape(columns, blocks * BLOCK)[:, rows:] = 0  # the padding, which no product reads
    return (values[..., 0::2] | values[..., 1::2] << 4), scales[..., 0]


class Weights:
    """separator.weights as it is written: each tensor's bytes one after another, and where each is."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.index: dict[str, dict] = {}

    def _add(self, data: bytes) -> int:
        self.data += bytes(-len(self.data) % 8)  # each tensor starts on 8 bytes
        self.data += data
        return len(self.data) - len(data)

    def matrix(self, name: str, weight: np.ndarray) -> None:
        packed, scales = quantised(np.ascontiguousarray(weight, np.float32))
        self.index[name] = {"kind": "q4", "shape": list(weight.shape), "at": self._add(packed.tobytes()),
                            "scales": self._add(scales.astype("<f4").tobytes())}

    def floats(self, name: str, values: np.ndarray) -> None:
        self.index[name] = {"kind": "f32", "shape": list(values.shape), "at": self._add(values.astype("<f4").tobytes())}


def _external(name: str, kind: int, shape: list[int], at: int, size: int) -> onnx.TensorProto:
    """An initialiser whose data is at a place in separator.weights, not in the graph."""
    tensor = TP(name=name, data_type=kind, dims=shape, data_location=TP.EXTERNAL)
    for key, value in {"location": WEIGHTS, "offset": str(at), "length": str(size)}.items():
        tensor.external_data.add(key=key, value=value)
    return tensor


class Graph:
    """The network for the CPU: MatMulNBits over the 4-bit matrices of separator.weights."""

    def __init__(self, weights: Weights) -> None:
        self.weights = weights
        self.nodes: list[onnx.NodeProto] = []
        self.initialisers: list[onnx.TensorProto] = []
        self.count = 0

    def _name(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}_{self.count}"

    def node(self, op: str, inputs: list[str], outputs: int = 1, domain: str = "", **attributes):
        names = [self._name(op) for _ in range(outputs)]
        self.nodes.append(h.make_node(op, inputs, names, name=self._name("n_" + op), domain=domain, **attributes))
        return names if outputs > 1 else names[0]

    def ints(self, values) -> str:
        name = self._name("i")
        self.initialisers.append(nh.from_array(np.asarray(values, np.int64), name))
        return name

    def floats(self, name: str) -> str:
        """A float tensor of separator.weights."""
        entry = self.weights.index[name]
        self.initialisers.append(_external(name, TP.FLOAT, entry["shape"], entry["at"], 4 * int(np.prod(entry["shape"]))))
        return name

    def matmul(self, x: str, name: str) -> str:
        entry = self.weights.index[name]
        rows, columns = entry["shape"]
        blocks = -(-rows // BLOCK)
        packed, scales = name + "_Q4", name + "_scales"
        self.initialisers.append(_external(packed, TP.UINT8, [columns, blocks, BLOCK // 2], entry["at"],
                                           columns * blocks * BLOCK // 2))
        self.initialisers.append(_external(scales, TP.FLOAT, [columns * blocks], entry["scales"], 4 * columns * blocks))
        return self.node("MatMulNBits", [x, packed, scales], domain="com.microsoft", K=rows, N=columns, bits=4,
                         block_size=BLOCK, accuracy_level=0)

    def linear(self, x: str, name: str) -> str:
        return self.node("Add", [self.matmul(x, name + ".weight"), self.floats(name + ".bias")])

    def norm(self, x: str, name: str) -> str:
        return self.node("SimplifiedLayerNormalization", [x, self.floats(name + ".gamma")], axis=-1, epsilon=EPSILON,
                         stash_type=1)

    def skip_norm(self, x: str, skip: str, name: str, with_sum: bool = True) -> tuple[str, str]:
        """(norm(x + skip), x + skip)."""
        outputs = [self._name("norm"), "", "", self._name("sum") if with_sum else ""]
        self.nodes.append(h.make_node("SkipSimplifiedLayerNormalization", [x, skip, self.floats(name + ".gamma")],
                                      outputs, name=self._name("n_skipnorm"), domain="com.microsoft", epsilon=EPSILON))
        return outputs[0], outputs[3]

    def concat(self, inputs: list[str], axis: int) -> str:
        while len(inputs) > GROUP:
            inputs = [self.node("Concat", inputs[i:i + GROUP], axis=axis) if len(inputs[i:i + GROUP]) > 1 else inputs[i]
                      for i in range(0, len(inputs), GROUP)]
        return self.node("Concat", inputs, axis=axis)

    def transformer(self, x: str, name: str, sequences: str, rotary: tuple[str, str, str]) -> str:
        """One transformer over x, (1, tokens, DIM), whose attention sees it in the shape sequences (sequences x
        length x HEADS * HEAD); returns its norm(out)."""
        attention, feed = f"{name}.layers.0.0", f"{name}.layers.0.1"
        normed = self.norm(x, attention + ".norm")
        q, k, v = (self.node("Reshape", [self.matmul(normed, f"{attention}.to_qkv.{part}"), sequences]) for part in "qkv")
        q, k = (self.node("RotaryEmbedding", [each, *rotary], domain="com.microsoft", interleaved=1, num_heads=HEADS)
                for each in (q, k))
        out = self.node("MultiHeadAttention", [q, k, v], domain="com.microsoft", num_heads=HEADS)
        gates = self.node("Sigmoid", [self.linear(normed, attention + ".to_gates")])  # (1, tokens, HEADS)
        out = self.node("Mul", [self.node("Reshape", [out, self.ints([1, -1, HEADS, HEAD])]),
                                self.node("Unsqueeze", [gates, self.ints([-1])])])
        out = self.matmul(self.node("Reshape", [out, self.ints([1, -1, HEADS * HEAD])]), attention + ".to_out.0.weight")
        normed, x = self.skip_norm(out, x, feed + ".net.0")
        hidden = self.node("Gelu", [self.linear(normed, feed + ".net.1")], domain="com.microsoft")
        return self.skip_norm(self.linear(hidden, feed + ".net.4"), x, name + ".norm", with_sum=False)[0]

    def build(self, bands: list[tuple[int, int]]) -> onnx.ModelProto:
        widths = [4 * bins for _, bins in bands]  # a band's inputs: its bins x 2 channels x (real, imaginary)
        picked = [2 * bin + channel for first, bins in bands for bin in range(first, first + bins) for channel in (0, 1)]
        shape = self.node("Shape", ["spec"])
        batch, frames = (self.node("Gather", [shape, self.ints([axis])], axis=0) for axis in (0, 2))
        x = self.node("Gather", ["spec", self.ints(picked)], axis=1)  # (batch, sum of bins x 2, frames, 2)
        x = self.node("Reshape", [self.node("Transpose", [x], perm=[0, 2, 1, 3]), self.ints([0, 0, sum(widths)])])
        features, at = [], 0
        for band, width in enumerate(widths):
            part = self.node("Slice", [x, self.ints([at]), self.ints([at + width]), self.ints([-1])])
            part = self.linear(self.norm(part, f"band_split.to_features.{band}.0"), f"band_split.to_features.{band}.1")
            features.append(self.node("Unsqueeze", [part, self.ints([2])]))
            at += width
        x = self.concat(features, 2)  # (batch, frames, BANDS, DIM)
        # A sequence's positions count from 0 (the one position id), whatever the batch and the frames.
        rotary = {which: (self.ints([0]), self.floats(f"rotary.{which}.cos"), self.floats(f"rotary.{which}.sin"))
                  for which in ("time", "frequency")}
        tokens = self.ints([1, -1, DIM])
        by_band = self.node("Concat", [batch, self.ints([BANDS]), frames, self.ints([DIM])], axis=0)
        by_frame = self.node("Concat", [batch, frames, self.ints([BANDS, DIM])], axis=0)
        over_time = self.node("Concat", [self.ints([-1]), frames, self.ints([HEADS * HEAD])], axis=0)
        over_bands = self.ints([-1, BANDS, HEADS * HEAD])
        for layer in range(6):
            x = self.node("Transpose", [x], perm=[0, 2, 1, 3])  # (batch, BANDS, frames, DIM)
            x = self.transformer(self.node("Reshape", [x, tokens]), f"layers.{layer}.0", over_time, rotary["time"])
            x = self.node("Transpose", [self.node("Reshape", [x, by_band])], perm=[0, 2, 1, 3])
            x = self.transformer(self.node("Reshape", [x, tokens]), f"layers.{layer}.1", over_bands, rotary["frequency"])
            x = self.node("Reshape", [x, by_frame])
        masks = []
        for band in range(BANDS):
            y = self.node("Gather", [x, self.ints(band)], axis=2)  # (batch, frames, DIM)
            for depth in range(3):
                y = self.linear(y, f"mask_estimators.0.to_freqs.{band}.0.{2 * depth}")
                if depth < 2:
                    y = self.node("Tanh", [y])
            value, gate = self.node("Split", [y], 2, axis=-1, num_outputs=2)
            masks.append(self.node("Mul", [value, self.node("Sigmoid", [gate])]))
        y = self.concat(masks, -1)
        self.nodes.append(h.make_node("Identity", [y], ["masks"], name="n_masks"))
        graph = h.make_graph(self.nodes, "separator",
                             [h.make_tensor_value_info("spec", TP.FLOAT, ["batch", 2 * BINS, "frames", 2])],
                             [h.make_tensor_value_info("masks", TP.FLOAT, ["batch", "frames", sum(widths)])],
                             self.initialisers)
        model = h.make_model(graph, opset_imports=[h.make_opsetid("", 23), h.make_opsetid("com.microsoft", 1)],
                             producer_name="wozmod")
        model.ir_version = 10
        return model


def tensors(checkpoint: dict[str, np.ndarray], bands: list[tuple[int, int]]) -> Weights:
    """separator.weights from the checkpoint: a Linear's weight transposed to (inputs, outputs) as MatMul takes it,
    the attention's one query-key-value matrix as three, and the rotary angles' cosines and sines by position."""
    for band, (_, bins) in enumerate(bands):
        if checkpoint[f"band_split.to_features.{band}.0.gamma"].shape != (4 * bins,):
            raise ValueError(f"mel band {band} has {bins} bins, which is not the checkpoint's band")
    weights = Weights()
    for name, value in checkpoint.items():
        if name.endswith("rotary_embed.freqs"):
            continue
        if name.endswith("to_qkv.weight"):
            for i, part in enumerate("qkv"):
                weights.matrix(f"{name[:-len('.weight')]}.{part}", value[i * HEADS * HEAD:(i + 1) * HEADS * HEAD].T)
        elif value.ndim == 2:
            weights.matrix(name, value.T)
        else:
            weights.floats(name, value)
    for which, layer, length in (("time", "layers.0.0", FRAMES), ("frequency", "layers.0.1", BANDS)):
        # each orientation's transformers share one rotary embedding: its angles are the same in every layer
        angles = np.arange(length)[:, None] * checkpoint[f"{layer}.layers.0.0.rotary_embed.freqs"].astype(np.float64)[None]
        weights.floats(f"rotary.{which}.cos", np.cos(angles))
        weights.floats(f"rotary.{which}.sin", np.sin(angles))
    return weights


def build(checkpoint: Path, folder: Path) -> dict[str, tuple[int, str]]:
    """Write the model files into folder; return each file's (size, SHA-256)."""
    bands = mel_bands()
    weights = tensors(read_checkpoint(checkpoint), bands)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / WEIGHTS).write_bytes(weights.data)
    (folder / GRAPH).write_bytes(Graph(weights).build(bands).SerializeToString(deterministic=True))
    index = {"block": BLOCK, "bands": bands, "tensors": weights.index}
    (folder / INDEX).write_text(json.dumps(index, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    return {name: ((folder / name).stat().st_size, hashlib.sha256((folder / name).read_bytes()).hexdigest())
            for name in (GRAPH, WEIGHTS, INDEX)}


if __name__ == "__main__":
    for name, (size, digest) in build(Path(sys.argv[1]), Path(sys.argv[2])).items():
        print(f'    "{name}": ({size:_}, "{digest}"),')
