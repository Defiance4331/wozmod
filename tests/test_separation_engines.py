"""The Dream voice separator's GPU engines against its CPU engine, the measure (slow: WOZMOD_AUDIO=1 with
WOZMOD_SEPARATOR; the test on real sound also needs WOZMOD_GAME and WOZMOD_DONORS, and a GPU). The CUDA engine is
a row of the real-sound test only where the native module is built with it (native/src/cuda.rs).

The Vulkan shaders run on any Vulkan device, so the one test that needs no GPU runs them on the software renderer
Mesa ships (lavapipe). Its limits are the tightest of any driver: 128 MB a buffer, 32 KB of shared memory, matrix
units of 8x8x8 in subgroups of 8.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
native = pytest.importorskip("wozmod_native")

from wozmod.voices import separation  # noqa: E402
from wozmod.voices.separation import CHUNK, HOP, Device, ShaderSet  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("WOZMOD_AUDIO") != "1" or not os.environ.get("WOZMOD_SEPARATOR"),
                                reason="set WOZMOD_AUDIO=1 and WOZMOD_SEPARATOR, the Dream voice model folder")
AGREE_DB = -40.0  # an engine's masks against the CPU's: -56 to -57 dB on real sound, -47 to -51 dB on a short noise
ROUNDING_DB = 2.0  # how far a GPU engine may be behind the one closest to the CPU on real sound: 1.1 dB measured
REAL_DB = -54.5  # and how far from the CPU on real sound at most: -55.9 to -57.1 dB measured, -51.8 to -53.6 dB
#                  where a conversion to 16-bit floats truncates


@pytest.fixture(scope="module")
def model() -> tuple[Path, dict]:
    folder = Path(os.environ["WOZMOD_SEPARATOR"])
    return folder, json.loads((folder / separation.INDEX).read_text(encoding="utf-8"))


def _devices() -> list[Device]:
    try:
        return Device.listed(native)[0]
    except RuntimeError:  # no Vulkan loader
        return []


def _sets(folder: Path, device: Device) -> list[ShaderSet]:
    return separation.shader_sets(device, lambda name: native.separator_shared_memory(str(folder / name)))


def _apart(network, spectra: list[bytes], cpu: list[bytes], frames: int) -> float:
    """The worst of a network's masks against the CPU's over the spectra, in dB; nan where one is not finite."""
    return max(native.separator_agreement(network.run(made, 1, frames), masks) for made, masks in zip(spectra, cpu, strict=True))


def test_the_vulkan_shaders_give_the_cpus_masks_on_a_software_renderer(model):
    # Bug: a shader computes another step than the CPU's (an offset in the wrong unit, a row stride, the wrong
    # activation, the rotary turn on the wrong columns); the matrix product is right only for the shape it was first
    # written for (16x16x16: here it is 8x8x8 in subgroups of 8); or the network, split over many buffers, binds a
    # tensor's neighbour or counts its place from another buffer's start. FRAMES is more than the 64 keys the
    # attention takes at a time, so its sums are carried from one run of keys to the next.
    folder, index = model
    device = next((each for each in _devices() if each.kind == "cpu"), None)
    if device is None:
        pytest.skip("no software Vulkan device (Mesa's lavapipe) is installed")
    frames = 72
    sound = (np.random.default_rng(5).standard_normal(((frames - 1) * HOP, 2)) * 0.1).astype("<f4")
    spectrum = native.separator_spectrum(sound.tobytes())
    cpu = separation.cpu_engine(native, folder, 2).start().run(spectrum, 1, frames)
    sets = _sets(folder, device)
    assert [each.tier for each in sets] == ["matrix", "float16", "float32"] and sets[0].matrix == (8, 8, 8)

    apart = {}
    for shaders in sets:
        network = separation.vulkan_network(native, folder, index, device, shaders, frames, limit=8 << 20)
        assert network.buffers > 16  # the split is what is tested
        apart[str(shaders)] = round(_apart(network, [spectrum], [cpu], frames), 1)
    assert all(db <= AGREE_DB for db in apart.values()), f"each shader set's masks against the CPU's, dB: {apart}"


@pytest.mark.skipif(not all(os.environ.get(name) for name in ("WOZMOD_GAME", "WOZMOD_DONORS")),
                    reason="set WOZMOD_GAME and WOZMOD_DONORS")
def test_every_gpu_engine_and_shader_set_gives_the_cpus_masks_on_real_sound(model):
    # Bug: an engine is right on a machine's first choice only. A matrix shape the driver offers besides the first, the attention on the plainer matrix units, the shader sets a card falls back to, or the
    # network split over smaller buffers go wrong on a real driver (a tile read past its tensor, a copy that races
    # a shader), and the players whose card takes that path hear it in every Dream line. Or conversions to 16-bit
    # floats truncate, as the NVIDIA driver's do unless a shader asks for the nearest: one path's, and it falls 3 to
    # 5 dB behind the others, or every path's, and all of them read worse than REAL_DB.
    from dream_inputs import scene_tracks
    from wozmod.voices.dream_events import DREAM_EVENTS

    folder, index = model
    sound = scene_tracks(min(DREAM_EVENTS))[4]
    spectra = [native.separator_spectrum(sound[8 * start:8 * (start + CHUNK)]) for start in (0, len(sound) // 16 - CHUNK // 2)]
    rows = {}
    cuda = separation.nvidia_engine(native, folder, index)  # a row only in a build that has the engine and its kernels
    try:
        cuda.facts()
        rows[cuda.name] = cuda.start
    except separation.Unavailable:
        pass
    for device in _devices():
        if separation.vulkan_problem(device):
            continue
        sets = _sets(folder, device)
        first = sets[0]
        more = [ShaderSet("matrix", first.attention, shape, first.lanes, first.fixed) for shape in device.matrices
                if first.tier == "matrix" and shape != first.matrix]
        if first.attention == "matrix2":  # the attention a card with 16x16x16 units and no matrix2 gets
            more.append(ShaderSet("matrix", "matrix", first.matrix, first.lanes, first.fixed))
        for shaders, limit in [*((each, None) for each in (*sets, *more)), (first, 64 << 20)]:
            name = f"Vulkan device {device.index}, {shaders}{', 64 MB buffers' if limit else ''}"
            rows[name] = lambda device=device, shaders=shaders, limit=limit: separation.vulkan_network(
                native, folder, index, device, shaders, limit=limit)
    if not rows:
        pytest.skip("this machine has no GPU an engine runs on")
    network = separation.cpu_engine(native, folder, os.process_cpu_count()).start()
    cpu = [network.run(made, 1, separation.FRAMES) for made in spectra]

    apart = {name: round(_apart(start(), spectra, cpu, separation.FRAMES), 1) for name, start in rows.items()}
    print(apart)
    assert all(db <= min(REAL_DB, min(apart.values()) + ROUNDING_DB) for db in apart.values()), \
        f"each engine's masks against the CPU's, dB: {apart}"
