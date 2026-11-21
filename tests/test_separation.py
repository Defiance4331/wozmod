"""The Dream voice separator around its network (voices/separation.py): the STFT, the masks' voice, the chunk plan
and join, which device runs the chunks, and the process it all runs in. Synthetic sound and stand-in networks; the
network itself, on real scenes, is tests/test_separation_fidelity.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
native = pytest.importorskip("wozmod_native")

from wozmod.voices import separation  # noqa: E402
from wozmod.voices.separation import (CHUNK, CONTEXT, Card, Device, Engine, ShaderSet, Unavailable, cover,  # noqa: E402
                                      gpu_problem, run_chunks, shader_sets, vulkan_problem)

FRAME, HOP, BINS = 2048, 441, 1025


def _stereo(rng, samples: int) -> np.ndarray:
    return rng.standard_normal((samples, 2)).astype("<f4")  # a row a sample: interleaved


def test_the_spectrum_is_the_models_stft_of_each_channel():
    # Bug: the frames are a sample off (a hop of 440, the sound not mirrored by 1024 at both ends, a symmetric Hann
    # for the periodic one), the channels are swapped, or the rows are not 2 * bin + channel: the network then reads
    # another spectrum than the one it was trained on, and the voice it leaves is wrong in every chunk.
    rng = np.random.default_rng(1)
    sound = _stereo(rng, 7 * HOP + 100)

    spec = np.frombuffer(native.separator_spectrum(sound.tobytes()), "<f4").reshape(2 * BINS, -1, 2)

    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FRAME) / FRAME)
    frames = 1 + len(sound) // HOP
    assert spec.shape[1] == frames
    for channel in (0, 1):
        padded = np.pad(sound[:, channel].astype(float), FRAME // 2, mode="reflect")
        expected = np.stack([np.fft.rfft(padded[t * HOP:t * HOP + FRAME] * window) for t in range(frames)], 1)
        found = spec[channel::2, :, 0] + 1j * spec[channel::2, :, 1]
        assert np.abs(found - expected).max() < 1e-3 * np.abs(expected).max(), channel


def test_masks_of_one_give_the_sound_back_where_bands_share_bins():
    # Bug: a bin two bands hold gets the sum of their masks, not the mean (the voice doubles there), the inverse
    # frames are added without the window's weight, or the channels cross: the round trip is then not the sound.
    rng = np.random.default_rng(2)
    sound = _stereo(rng, 9 * HOP)
    bands = [(0, 300), (250, 500), (700, 325)]  # the first two share bins 250-299; together every bin
    spec = native.separator_spectrum(sound.tobytes())
    frames = 1 + len(sound) // HOP
    ones = np.zeros((frames, sum(4 * bins for _, bins in bands) // 2, 2), "<f4")
    ones[..., 0] = 1.0  # (1 + 0j) for every band bin and channel

    voice = np.frombuffer(native.separator_voice(spec, ones.tobytes(), bands, len(sound)), "<f4").reshape(-1, 2)

    assert np.abs(voice - sound).max() < 1e-4


def test_the_chunks_hold_every_wanted_sample_with_its_context_and_join_into_the_sound():
    # Bug: a wanted sample falls between two chunks or within CONTEXT of a chunk's edge (the network hears it with
    # no sound before it), the last chunk runs past the sound, or the join adds both chunks where they overlap
    # (the voice doubles) or fades one without the other (it dips): a network that changes nothing must give the
    # sound back on every sample a chunk holds.
    rng = np.random.default_rng(3)
    length = 3 * CHUNK + 12345
    spans = [(CONTEXT // 2, 40_000), (CHUNK - 10, CHUNK + 5000), (2 * CHUNK + 60_000, length)]
    sound = _stereo(rng, length)

    starts = cover(length, spans)

    assert starts == sorted(set(starts)) and all(0 <= s <= length - CHUNK for s in starts)
    for a, b in spans:
        for sample in (a, (a + b) // 2, b - 1):
            assert any(s <= max(sample - CONTEXT, 0) and min(sample + CONTEXT, length - 1) < s + CHUNK for s in starts), sample
    joined = native.separator_joined(length, [(s, sound[s:s + CHUNK].tobytes()) for s in starts])
    joined = np.frombuffer(joined, "<f4").reshape(-1, 2)
    held = np.zeros(length, bool)
    for s in starts:
        held[s:s + CHUNK] = True
    assert np.abs(joined[held] - sound[held]).max() < 1e-6 and not joined[~held].any()
    assert cover(CHUNK // 2, [(0, CHUNK // 2)]) == [0]  # a sound shorter than a chunk: one chunk, from its start
    # A sound just under two chunks: its last chunk is pulled back to end with it, over both chunks before it, and
    # where three chunks share samples their weights no longer add up to 1 by themselves.
    short = sound[:2 * CHUNK - CONTEXT]
    starts = cover(len(short), [(0, len(short))])
    assert len(starts) == 3 and starts[2] < starts[0] + CHUNK
    joined = native.separator_joined(len(short), [(s, short[s:s + CHUNK].tobytes()) for s in starts])
    assert np.abs(np.frombuffer(joined, "<f4").reshape(-1, 2) - short).max() < 1e-6


class _Net:
    """A stand-in network: its masks are a function of the spectrum alone, so two of them agree. It fails from its
    fail_at-th run on, or in any run while fail_when() holds."""

    def __init__(self, seconds: float = 0.0, fail_at: int | None = None, scale: float = 1.0, fail_when=lambda: False):
        self.seconds, self.fail_at, self.scale, self.fail_when, self.runs = seconds, fail_at, scale, fail_when, 0

    def run(self, spectra: bytes, chunks: int, frames: int) -> bytes:
        self.runs += 1
        if (self.fail_at is not None and self.runs >= self.fail_at) or self.fail_when():
            raise RuntimeError("a GPU run: the device was lost")
        time.sleep(self.seconds)
        return (np.frombuffer(spectra, "<f4")[::2050] * self.scale).astype("<f4").tobytes()


def _spectrum(i: int) -> bytes:
    return np.full(2050 * 2 * 3, i + 1, "<f4").tobytes()  # three frames


def _engine(name: str, start, facts=lambda: "a stand-in") -> Engine:
    return Engine(name, facts, start)


class _Sink:
    """Where run_chunks' results go: finished[i] is every masks finished for chunk i; what an engine ran ahead of its
    check is held until settle keeps it or throws it away."""

    def __init__(self) -> None:
        self.finished: dict[int, list[bytes]] = {}
        self.held: dict[int, list[bytes]] = {}
        self.kept = threading.Event()  # set once an engine's check has passed

    def finish(self, i: int, made: bytes, masks: bytes, held: bool) -> None:
        (self.held if held else self.finished).setdefault(i, []).append(masks)

    def settle(self, keep: bool) -> None:
        for i, masks in self.held.items() if keep else ():
            self.finished.setdefault(i, []).extend(masks)
        self.held = {}
        if keep:
            self.kept.set()


def _run(others, count: int = 12, cpu: float = 0.02, sink: _Sink | None = None):
    sink, log = sink or _Sink(), []
    how = run_chunks(count, _spectrum, sink.finish, _engine("CPU", lambda: _Net(cpu)), others, log=log.append,
                     settle=sink.settle)
    assert not sink.held  # nothing is left undecided
    return sink.finished, how, log


def test_an_engine_error_mid_run_hands_its_chunk_back_and_the_output_is_the_cpus():
    # Bug: a GPU error stops the separation (the install fails on a driver fault), the chunk the engine held when it
    # failed is lost or finished twice, what it ran ahead of a check it never reached is kept, or the CPU never
    # wakes to take the rest.
    expected, _, _ = _run([])

    for fail_at in (3, 4, 5, 8):  # its check is runs 1 and 2: it fails while it runs ahead, or after it took over
        finished, _, log = _run([_engine("GPU", lambda fail_at=fail_at: _Net(0.005, fail_at=fail_at))])
        assert finished == expected and any("device was lost" in line for line in log), fail_at

    sink = _Sink()
    finished, how, log = _run([_engine("GPU", lambda: _Net(0.005, fail_when=sink.kept.is_set))], sink=sink)
    assert finished == expected and "GPU, then the CPU after it failed" in how
    assert any("GPU used from here" in line for line in log)  # it had taken over: the fault was mid-run


def test_an_engine_that_passes_takes_every_chunk_after_the_first():
    # Bug: the CPU starts its second chunk while the engine's check is being read, so which chunks the CPU ran
    # depends on which thread got there first, and two installs on one machine write different voice files.
    for _ in range(5):
        _, how, _ = _run([_engine("GPU", _Net)])
        assert how == "GPU (1 chunks on the CPU, 11 on another engine)"


@pytest.mark.parametrize("gpu, why", [
    (lambda: _Net(scale=1.1), "masks -20.0 dB"),  # a driver that computes something else
    (lambda: _Net(0.2), "s a chunk"),  # slower than the CPU: an integrated GPU, or video memory paged to disk
])
def test_an_engine_whose_masks_differ_or_that_is_slower_than_the_cpu_is_not_used(gpu, why):
    # Bug: the check compares nothing (a GPU with a broken driver gives every Dream line a wrong voice), or keeps an
    # engine slower than the CPU (the install takes longer than without it).
    expected, _, _ = _run([])

    finished, how, log = _run([_engine("GPU", gpu)])

    assert finished == expected and how == "CPU (12 chunks on the CPU, 0 on another engine)"
    assert any("GPU not used" in line and why in line for line in log)


def test_what_an_engine_runs_ahead_of_its_check_counts_only_once_it_passes():
    # Bug: an engine waits for the CPU's first chunk before it runs the second (a fast GPU idles through a third of
    # its step); or what it ran ahead is kept though its check failed (a wrong voice in those chunks), or lost or
    # finished twice though it passed.
    expected, _, _ = _run([])
    near = np.frombuffer(_Net(scale=1.0005).run(_spectrum(3), 1, 3), "<f4").tobytes()  # -66 dB: passes, and tells

    finished, how, log = _run([_engine("GPU", lambda: _Net(scale=1.0005))], cpu=0.3)
    assert sum("on the GPU" in line and "held for the check" in line for line in log) >= 2
    assert how == "GPU (1 chunks on the CPU, 11 on another engine)"
    assert finished[0] == expected[0] and finished[3] == [near] and all(len(masks) == 1 for masks in finished.values())

    finished, how, log = _run([_engine("GPU", lambda: _Net(scale=1.1))], cpu=0.3)
    assert sum("on the GPU" in line and "held for the check" in line for line in log) >= 2
    assert finished == expected and how == "CPU (12 chunks on the CPU, 0 on another engine)"


def test_a_rejected_engines_network_is_let_go_before_the_next_engine_starts():
    # Bug: an engine that failed its check, or failed outright, keeps its network (1.4 GB of a card's memory) while
    # the next engine starts: on a card with less than twice that free, the next engine cannot start and the
    # player is left on the CPU.
    live, at_start = [0], []

    class Counted(_Net):
        def __init__(self, **how) -> None:
            super().__init__(**how)
            live[0] += 1

        def __del__(self) -> None:
            live[0] -= 1

    def started(**how):
        def start() -> Counted:
            at_start.append(live[0])
            return Counted(**how)
        return start

    _, how, _ = _run([_engine("differs", started(scale=1.1)), _engine("breaks", started(fail_at=2)),
                      _engine("slow", started(seconds=0.2)), _engine("good", started())])

    assert how.startswith("good (") and at_start == [0, 0, 0, 0]


def test_an_engine_that_stops_answering_is_given_up_and_what_is_finished_stays():
    # Bug: a graphics driver that hangs in a run hangs the install for good (no engine error comes, so the CPU
    # rests forever); or giving it up loses what was finished before it: the CPU's first chunk, the engine's.
    release = threading.Event()
    reasons: list[str] = []
    sink = _Sink()

    class Stalls(_Net):
        def run(self, spectra: bytes, chunks: int, frames: int) -> bytes:
            if sink.kept.is_set() and len(sink.finished) >= 4:  # it has taken over, and stops a few chunks on
                if not release.wait(5):
                    raise AssertionError("never given up")
                raise RuntimeError("the driver came back after the process was to end")
            return super().run(spectra, chunks, frames)

    class Ended(Exception):
        pass

    def abandon(why: str) -> None:  # the separating process ends here; the test lets the stalled run go instead
        reasons.append(why)
        release.set()
        raise Ended

    with pytest.raises(Ended):
        run_chunks(12, _spectrum, sink.finish, _engine("CPU", lambda: _Net(0.05)), [_engine("GPU", lambda: Stalls(0.01))],
                   settle=sink.settle, abandon=abandon)

    assert len(reasons) == 1 and reasons[0].startswith("GPU given up: nothing from it for")
    assert 0 in sink.finished and 4 <= len(sink.finished) < 12 and all(len(masks) == 1 for masks in sink.finished.values())
    assert all(masks == [_Net().run(_spectrum(i), 1, 3)] for i, masks in sink.finished.items())


@pytest.mark.parametrize("waited, left, given_up", [
    (58.0, 10, False), (58.3, 10, True),  # 10 chunks left: the CPU would need 60 s, the engine 1.8 s for the other 9
    (5.9, 1, False), (6.1, 1, True),  # the last chunk: one CPU chunk's time
    (5.9, 0, False), (6.1, 0, True),  # nothing left: it gets as long to come back
])
def test_an_engine_is_given_up_when_it_can_no_longer_finish_before_the_cpu_would_have(waited, left, given_up):
    # Bug: the wait is a fixed time (a healthy engine on a slow machine is killed, or a short job waits minutes),
    # or it does not shrink with the chunks left, so a hang near the end costs as much as one at the start.
    assert (separation._overdue("GPU", waited, left, cpu=6.0, each=0.2) is not None) == given_up


def test_a_device_whose_facts_cannot_be_read_does_not_hide_the_others(tmp_path, monkeypatch):
    # Bug: one Vulkan device the driver cannot describe (a failing query) makes the whole list fail, so a good GPU
    # beside it is never tried and the player gets the CPU.
    good = ("a GPU", "discrete", 0x1002, "a driver", (1, 3, 280), (64, 64, 64), [], (False, True, True), (1 << 31, 65_536, 6 << 30))

    class Module:
        separator_vulkan_devices = staticmethod(lambda: [(None, "its matrix shapes: ERROR_UNKNOWN"), (good, None)])
        separator_shared_memory = staticmethod(lambda file: 0)

    monkeypatch.setattr(separation, "SHADERS", {})
    said: list[str] = []

    engines = separation.vulkan_engines(Module, tmp_path, {}, said.append)

    assert [engine.name for engine in engines] == ["Vulkan GPU 1 (float16)", "Vulkan GPU 1 (float32)"]
    assert said == ["Vulkan device 0 not tried: what it can do could not be read: its matrix shapes: ERROR_UNKNOWN"]


def test_an_engine_that_cannot_be_tried_or_does_not_start_gives_way_to_the_next():
    # Bug: one engine that is not there (no driver) or fails to start (a kernel the driver refuses) sends every
    # chunk to the CPU though the next engine runs, or its reason never reaches the install log.
    def absent() -> str:
        raise Unavailable("no NVIDIA driver library")

    def broken():
        raise RuntimeError("the driver refused the kernels")

    finished, how, log = _run([_engine("first", _Net, absent), _engine("second", broken), _engine("third", _Net)])

    assert how.startswith("third (") and finished == _run([])[0]
    assert "first not tried: no NVIDIA driver library" in log and "second failed: the driver refused the kernels" in log
    assert "third: a stand-in" in log and "CPU: a stand-in" in log  # what each runs on is said


def test_which_nvidia_gpus_get_the_cuda_kernels():
    # Bug: a card without the kernels' instructions is tried (GTX 10: every launch fails after the weights are
    # loaded), or one with too little free video memory is (it runs out mid-run).
    def card(capability=(7, 5), free=4000) -> Card:
        return Card("620.70", 13050, "a card", capability, free * 2 ** 20, 8000 * 2 ** 20)

    assert gpu_problem(card()) is None and gpu_problem(card((8, 9), 1600)) is None
    assert "compute capability 6.1" in gpu_problem(card((6, 1)))
    assert "video memory" in gpu_problem(card(free=1599))


# What the shaders declare of shared memory, by file (native separator_shared_memory of the pinned files).
SHARED = {"separator-products-matrix.spv": 23_552, "separator-products-float16.spv": 8_192,
          "separator-products-float32.spv": 16_896, "separator-attention-matrix2.spv": 18_432,
          "separator-attention-matrix.spv": 45_056, "separator-attention-plain.spv": 32_768}


def _device(**facts) -> Device:
    turing = dict(index=0, name="a GPU", kind="discrete", vendor=separation.NVIDIA, driver="a driver", version=(1, 4, 300),
                  subgroup=(32, 32, 32), matrices=((16, 16, 16), (16, 8, 16), (16, 8, 8)), matrix2=True, float16=True,
                  full_subgroups=True, buffer_limit=1 << 32, shared_limit=49_152, free=6 << 30)
    return Device(**turing | facts)


@pytest.mark.parametrize("facts, sets", [
    # an NVIDIA card with matrix units and VK_NV_cooperative_matrix2 (RTX 20 and later)
    ({}, [("matrix", "matrix2", (16, 16, 16)), ("float16", "plain", None), ("float32", "plain", None)]),
    # the same without matrix2 (an older driver, or another maker's units)
    ({"matrix2": False}, [("matrix", "matrix", (16, 16, 16)), ("float16", "plain", None), ("float32", "plain", None)]),
    # ... whose shared memory the matrix attention does not fit
    ({"matrix2": False, "shared_limit": 32_768}, [("matrix", "plain", (16, 16, 16)), ("float16", "plain", None), ("float32", "plain", None)]),
    # matrix units in other shapes than the attention's, with subgroups of 16: the products take the largest shape
    # their shader is written for
    ({"vendor": 0x8086, "matrices": ((8, 8, 16), (8, 16, 32), (4, 4, 4), (16, 32, 32)), "matrix2": False, "subgroup": (16, 8, 16)},
     [("matrix", "plain", (8, 16, 32)), ("float16", "plain", None), ("float32", "plain", None)]),
    # a device with a choice of subgroup sizes whose own is 32 (Intel: 8 to 32): the size is still asked for
    ({"vendor": 0x8086, "matrices": (), "matrix2": False, "subgroup": (32, 8, 32)}, [("float16", "plain", None), ("float32", "plain", None)]),
    # a 16x16x16 shape in subgroups the attention's 32 lanes cannot be had in
    ({"vendor": 0x1002, "matrix2": False, "subgroup": (64, 64, 64)}, [("matrix", "plain", (16, 16, 16)), ("float16", "plain", None), ("float32", "plain", None)]),
    # no matrix units: 16-bit floats, except on an NVIDIA card, which computes with them at 1/64 rate
    ({"vendor": 0x1002, "matrices": (), "matrix2": False, "subgroup": (64, 32, 64)}, [("float16", "plain", None), ("float32", "plain", None)]),
    ({"matrices": (), "matrix2": False}, [("float32", "plain", None)]),
    ({"vendor": 0x8086, "matrices": (), "matrix2": False, "float16": False}, [("float32", "plain", None)]),
    # shared memory under what the plain attention needs: nothing runs
    ({"vendor": 0x1002, "matrices": (), "matrix2": False, "shared_limit": 16_384}, []),
])
def test_a_device_gets_the_shader_sets_it_can_run_fastest_first(facts, sets):
    # Bug: a device is given a shader it cannot run (the matrix attention without 16x16x16 units or 32-lane
    # subgroups, a shape the products shader does not take, a shader over its shared memory, which faults the
    # driver), 16-bit products where they run at 1/64 rate, or no set although one would run, which leaves a
    # player's GPU idle through a 7 minute step. Or a device that has a choice of subgroup sizes is not asked for the
    # one the shaders are built for: its driver may then run them in another (SPIR-V 1.6 lets it), and a shader
    # written for 8 subgroups of 32 writes past its tile.
    device = _device(**facts)
    lanes = 32 if device.subgroup[1] <= 32 <= device.subgroup[2] else device.subgroup[0]
    fixed = device.subgroup[1] < device.subgroup[2]
    assert shader_sets(device, SHARED.__getitem__) == [ShaderSet(tier, attention, matrix, lanes, fixed) for tier, attention, matrix in sets]


def test_which_vulkan_devices_are_tried():
    # Bug: a software renderer is taken for a GPU (lavapipe: 90 s a chunk, behind a first chunk that looks right),
    # or a device whose driver cannot build the shaders, or that has too little memory left, is started.
    assert vulkan_problem(_device()) is None and vulkan_problem(_device(kind="integrated", free=1600 << 20)) is None
    assert "not a GPU" in vulkan_problem(_device(kind="cpu"))
    assert "Vulkan 1.3" in vulkan_problem(_device(version=(1, 2, 198)))
    assert "subgroups" in vulkan_problem(_device(full_subgroups=False))
    assert "memory" in vulkan_problem(_device(free=1599 << 20))


def test_a_shaders_shared_memory_is_counted_as_vulkan_bounds_it(tmp_path):
    # Bug: an array of arrays is counted as its inner array, a three-part vector as 12 bytes a row instead of 16,
    # or a variable outside shared memory is counted: a shader over the device's limit then reaches the driver,
    # which faults at the first dispatch (NVIDIA: Xid 13), or one that fits is refused.
    def module(*instructions) -> bytes:
        words = [0x07230203, 0x00010600, 0, 100, 0]
        for op, *operands in instructions:
            words += [(len(operands) + 1) << 16 | op, *operands]
        return struct.pack(f"<{len(words)}I", *words)

    workgroup, private = 4, 6
    spirv = module(
        (22, 1, 32), (23, 2, 1, 4), (21, 3, 32, 0), (43, 3, 4, 8), (43, 3, 5, 64),  # float, vec4, uint, 8, 64
        (28, 6, 2, 4), (28, 7, 6, 5), (32, 8, workgroup, 7), (59, 8, 9, workgroup),  # shared vec4[64][8]: 8192
        (23, 10, 1, 3), (43, 3, 11, 5), (28, 12, 10, 11), (32, 13, workgroup, 12), (59, 13, 14, workgroup),  # vec3[5]: 80
        (32, 15, workgroup, 1), (59, 15, 16, workgroup),  # a float: 4
        (32, 17, private, 7), (59, 17, 18, private))  # not shared
    (tmp_path / "shader.spv").write_bytes(spirv)
    assert native.separator_shared_memory(str(tmp_path / "shader.spv")) == 8192 + 80 + 4


@pytest.mark.skipif(not os.environ.get("WOZMOD_SEPARATOR"), reason="set WOZMOD_SEPARATOR to the Dream voice model folder")
def test_the_cpu_engine_loads_the_shipped_model_and_takes_any_number_of_chunks_and_frames():
    # Bug: ONNX Runtime's library does not load on this system (its file name, or the libraries it needs, are looked
    # for elsewhere), the graph names an operator or a tensor the pinned runtime and weights lack, or a fixed size
    # is left in the graph (a chunk shorter than 8 s, or two at once, then fails or reads another chunk's frames).
    network = separation.cpu_engine(native, Path(os.environ["WOZMOD_SEPARATOR"]), 2).start()
    rng = np.random.default_rng(5)
    short, other, longer = (native.separator_spectrum(_stereo(rng, frames * HOP).tobytes()) for frames in (9, 9, 22))
    width = len(network.run(short, 1, 10)) // (4 * 10)

    alone = np.frombuffer(network.run(short, 1, 10), "<f4")
    assert width == 7916 and np.isfinite(alone).all() and alone.any()
    assert len(network.run(longer, 1, 23)) == 4 * 23 * width
    both = np.frombuffer(network.run(short + other, 2, 10), "<f4")
    assert np.abs(both[:len(alone)] - alone).max() < 1e-4 * np.abs(alone).max()


def test_the_model_must_be_the_pinned_files(tmp_path, monkeypatch):
    # Bug: another model file is accepted (a damaged download, a file from another release): the Dream voices would
    # come from weights the separation was never measured on, or the install would fail minutes in.
    library = separation.runtime(tmp_path)
    library.parent.mkdir()
    (tmp_path / "weights").write_bytes(b"weights")
    monkeypatch.setattr(separation, "MODEL", {"weights": (7, hashlib.sha256(b"weights").hexdigest())})

    assert separation.model_problem(tmp_path) == f"{library.parent.name}/{library.name} is missing"
    library.write_bytes(b"")
    assert separation.model_problem(tmp_path) is None
    (tmp_path / "weights").write_bytes(b"Weights")
    assert "weights is not the supported file" in separation.model_problem(tmp_path)


FAKE = '''
import json, os, sys
job = json.load(open(sys.argv[1]))
out, ran = job["out"], []
for k, (name, start) in enumerate(job["chunks"]):
    if os.path.exists(f"{out}/{k}.f32"):
        continue
    sound = open(job["sounds"][name], "rb").read()[8 * start:8 * (start + CHUNK)]
    open(f"{out}/{k}.f32", "wb").write(sound + bytes(8 * CHUNK - len(sound)))  # a network that changes nothing
    print(k, flush=True)
    ran.append(k)
    if job["gpu"] and len(ran) == 2:
        open(LOG, "a").write(json.dumps([job["gpu"], ran]) + "\\n")
        ENDING
open(LOG, "a").write(json.dumps([job["gpu"], ran]) + "\\n")
'''


@pytest.mark.parametrize("ending", [
    "os.kill(os.getpid(), 9)",  # a driver crash: no error is caught, nothing is said
    f"os._exit({separation.STALLED})",  # a driver that stopped answering: the process gave its engine up
])
def test_a_separating_process_that_crashes_is_finished_by_a_second_on_the_cpu(tmp_path, monkeypatch, ending):
    # Bug: a graphics driver that takes its process down, or that the process gave up waiting for, fails the
    # install, the second process tries the GPU again (and ends the same way), or it runs the chunks the first had
    # finished a second time.
    rng = np.random.default_rng(4)
    sound = _stereo(rng, 3 * CHUNK)
    log = tmp_path / "ran.jsonl"
    script = tmp_path / "fake.py"
    script.write_text(f"CHUNK, LOG = {CHUNK}, {str(log)!r}\n" + FAKE.replace("ENDING", ending), encoding="utf-8")
    monkeypatch.setattr(separation, "_command", lambda job: [sys.executable, str(script), str(job)])
    heard: list[tuple[int, int]] = []

    voices = separation.separate({"402": sound.tobytes()}, {"402": [(0, 3 * CHUNK)]}, tmp_path, 4,
                                 lambda done, total: heard.append((done, total)))

    count = len(cover(3 * CHUNK, [(0, 3 * CHUNK)]))
    assert count > 3 and [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] == [
        [True, [0, 1]], [False, list(range(2, count))]]
    assert heard == [(k, count) for k in range(1, count + 1)]
    assert np.abs(np.frombuffer(voices["402"], "<f4").reshape(-1, 2) - sound).max() < 1e-6


def test_a_separating_process_that_fails_with_an_error_of_its_own_stops_the_build(tmp_path, monkeypatch):
    # Bug: an error the process caught and said (a model file it cannot read) is taken for a driver crash and run
    # again on the CPU, where it fails the same way minutes later, or is swallowed and the takes come out silent.
    script = tmp_path / "fake.py"
    script.write_text(f"import sys; sys.exit({separation.FAILED})\n", encoding="utf-8")
    runs = []
    monkeypatch.setattr(separation, "_command", lambda job: runs.append(job) or [sys.executable, str(script)])

    with pytest.raises(separation.SeparationError):
        separation.separate({"402": bytes(8 * CHUNK)}, {"402": [(0, CHUNK)]}, Path(tmp_path), 4)
    assert len(runs) == 1
