"""The voice of a stretch of sound, without its music: Mel-Band RoFormer (Kimberley Jensen's vocal model), run from
the wozmod-native package, for the Z Dream scenes' English (dream_events).

The model takes 8 s chunks of 44.1 kHz stereo. Per chunk: its STFT (native separator_spectrum), the network's masks
for it, and the sound the masks leave (native separator_voice). The network is the one seam with a runtime behind it
(Network: spectra in, masks out), and an Engine is a way to run it: a runtime on a device, which says what it would
run on and makes its network. The STFT, the chunk plan and the choice between engines know nothing else of them.

Only the sound the takes need is separated: cover() places chunks so every wanted sample has CONTEXT of real sound on
both sides, and neighbouring chunks fade into each other over what they share (native separator_joined).

The CPU engine (ONNX Runtime) runs on every machine and its output is the measure. Any other engine is tried beside
it (run_chunks):

1. The engine says what it would run on, or why it cannot be tried on this machine.
2. While the CPU works on the first chunk, the engine runs it too, then runs ahead through the chunks after it. It
   takes over, and what it ran ahead counts, only if its masks for the first chunk are finite and within AGREE_DB of
   the CPU's, and a chunk takes it less time than the CPU. Otherwise what it made is thrown away.
3. An error at any point hands its chunk back to the CPU, which carries on from there.
4. An engine that stops answering is given up once it can no longer finish sooner than the CPU would have (_overdue),
   and the process ends: the chunks finished so far are kept, and a second process runs the rest on the CPU.

The other engines are GPU engines, and gpu_engines() is the one place that lists them, in the order they are tried,
the same on every system and every card: our Vulkan shaders on each GPU the system's Vulkan loader lists
(vulkan_engines), discrete before integrated. The shaders come in sets, picked from what the device says of itself
(shader_sets): products on its matrix units in whatever shape it has them, on 16-bit floats, or on 32-bit floats.
A GPU engine is tried only where the model folder holds its shaders as pinned.

A second GPU engine, our CUDA kernels on an NVIDIA card (nvidia_engine), is kept in the source and out of every
build: on the card it was measured on the Vulkan shaders run a chunk in the same time (owner's decision,
2026-11-21). native/src/cuda.rs says how to turn it back on.

All of it runs in a process of its own (separate), so a graphics driver that crashes or stops answering takes that
process with it and not the install: the chunks it finished are kept, and a second process finishes the rest on the
CPU.

What each choice was and why goes to the install log: each engine's name and what it runs on (the CPU's threads; a
Vulkan device's maker, name, kind, driver and its version, matrix shapes, 16-bit float support, subgroup sizes, free
memory, buffer and shared-memory limits, and the shaders picked), why one was not tried, not used or given up, the
first chunk's numbers, any error's text, each chunk's time with the device's share of it, and the total.
"""

from __future__ import annotations

import hashlib
import json
import queue
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from wozmod import native, platform
from wozmod.workers import ordered_map

RATE = 44_100  # samples a second the model takes, which is the PSP movies' own
CHUNK = 8 * RATE  # the samples a channel the model was trained on
HOP = 441  # samples between STFT frames
FRAMES = CHUNK // HOP + 1
CONTEXT = RATE // 4  # real sound kept on each side of a wanted sample: 0.1 s to 1 s all measured the same (0.02 dB)

NVIDIA = 0x10DE  # the maker's id, as Vulkan devices give it
SMALLEST_CAPABILITY = (7, 5)  # compute capability: the first with ldmatrix and mma.m16n8k8
FREE_VIDEO_MEMORY = 1_600 * 2 ** 20  # bytes the GPU must have free: the network takes 1.4 GB
AGREE_DB = -30.0  # the GPU's masks against the CPU's on one chunk: -58 dB measured

# The model folder: each file it needs, (bytes, SHA-256). packaging/separator_model.py makes them from Kimberley
# Jensen's checkpoint (MIT; packaging/SEPARATOR-NOTICE.txt), and the separation was measured on exactly these.
GRAPH, WEIGHTS, INDEX = "separator.onnx", "separator.weights", "separator.json"
MODEL = {
    "separator.onnx": (336_490, "6866500e1290aa83aa93161b51f92952230c82f2e057659d70b225c08986ce6f"),
    "separator.weights": (129_925_008, "98be9a9f5cf9201540f4087ac865c21dffd5dc034b64d9cca1333b1b8c2f240d"),
    "separator.json": (64_656, "9822a53df2a03249060c1c0fbcd8ec7704595388fa61e6023b4d4bada491f38d"),
}
# The CUDA engine's kernels, in the order a driver is offered them (the newest compiler's PTX first, then the one
# older drivers take), pinned the same way. packaging/separator_kernels.py --cuda makes them from
# packaging/separator/kernels.cu. No release carries them: the engine is out of every build (native/src/cuda.rs).
KERNELS = {
    "separator-cuda13.ptx": (722_896, "474ccc117f5afade1cebc8bd801e113d7c6676c71d866e9ee8ff550b2cfbd2be"),
    "separator-cuda12.ptx": (785_711, "3f6d921e5c7fea78b343dbb42a5d76e10496d9ab088c19feaa92c80a23fda0cf"),
}
# The Vulkan engine's shaders, pinned the same way: packaging/separator_kernels.py compiles them from
# packaging/separator/vulkan/. Which of them a device runs is shader_sets' choice.
SHADERS = {
    "separator-products-matrix.spv": (34_496, "07d211a62317b3799b6631813b3ad2618aa777bece20432a984146b95895dce1"),
    "separator-products-float16.spv": (83_972, "624e94f6ad85ae8e2bc9afd8cb61ba465643fe94cc406f5b39c2a7fcfdfa680e"),
    "separator-products-float32.spv": (125_820, "ae5aefc224318d6f12d4b10fb4b9358d2814dfe75eb66dbdb129e921178e7edc"),
    "separator-attention-matrix2.spv": (20_996, "21555f8b3a4f748822eb393da1520cb863231efd7cb28a27c1bd324eeef6bd41"),
    "separator-attention-matrix.spv": (38_808, "c4445ff34d098a93695119563e86ee597ed7d5d2879d98eb3bff34b36c5045af"),
    "separator-attention-plain.spv": (84_096, "0fb7be90cc59b38d4d42a956bb03778ee3c85c70cdf3e4fa41af69cd4f1deb8a"),
    "separator-norm.spv": (6_688, "648b27d91f80519c176623b6184b3ac6f8a2401f5cb763214c7136702e29012f"),
    "separator-gates.spv": (8_512, "fec3a37dcf3530c357ddb82e3777c6b7ee973a2dc6b6472bc1b1f0d95c127909"),
    "separator-bandsplit.spv": (8_564, "37728d0219891dd3efe056e49d5e9a73afc6b938742d39b93ecac7e3b85cfb6f"),
    "separator-glu.spv": (6_692, "cc2a704a6e0ec30f925189d3f9a966d822d22875740403fb42449faac5dc017d"),
}
# Each products shader's file and the (rows, columns) of a product one of its workgroups computes; each attention
# shader's file and the queries one of its workgroups takes. The shader sources say the same.
PRODUCTS = {"matrix": ("separator-products-matrix.spv", (128, 64)), "float16": ("separator-products-float16.spv", (128, 128)),
            "float32": ("separator-products-float32.spv", (128, 128))}
ATTENTION = {"matrix2": ("separator-attention-matrix2.spv", 64), "matrix": ("separator-attention-matrix.spv", 64),
             "plain": ("separator-attention-plain.spv", 32)}
STEPS = {"norm": "separator-norm.spv", "gates": "separator-gates.spv", "bandsplit": "separator-bandsplit.spv",
         "glu": "separator-glu.spv"}
VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel", 0x13B5: "ARM", 0x5143: "Qualcomm", 0x106B: "Apple",
           0x10005: "Mesa"}
ONNX_RUNTIME = "1.32.0"  # its library is in the model folder's subfolder for the system (packaging/pins.json pins it)
WORKER = "--separator-worker"  # the release program's argument that makes it the separating process (__main__)
_BOOT = "import sys; sys.path[:] = sys.argv[1:-1]; from wozmod.voices.separation import worker; worker(sys.argv[-1])"
LOG = "Dream voices: "  # how each of this module's lines in the install log starts


class SeparationError(RuntimeError):
    """The separation could not run."""


def model_problem(folder: Path | str | None) -> str | None:
    """None when folder holds every MODEL file as pinned and this system's ONNX Runtime, else what is wrong."""
    if folder is None or not Path(folder).is_dir():
        return "folder not found"
    if problem := pinned_problem(Path(folder), MODEL):
        return problem
    library = runtime(Path(folder))
    return None if library.is_file() else f"{library.parent.name}/{library.name} is missing"


def pinned_problem(folder: Path, files: dict[str, tuple[int, str]]) -> str | None:
    """None when folder holds each of files as pinned (name: (bytes, SHA-256)), else what is wrong with the first
    that is not."""
    for name, (size, sha256) in files.items():
        path = folder / name
        if not path.is_file():
            return f"{name} is missing"
        with path.open("rb") as stream:
            if path.stat().st_size != size or hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
                return f"{name} is not the supported file (wrong size or SHA-256)"
    return None


def runtime(folder: Path) -> Path:
    """ONNX Runtime's library for this system in the model folder."""
    return folder / platform.release_name() / platform.shared_library("onnxruntime", ONNX_RUNTIME)


def cover(length: int, spans: Sequence[tuple[int, int]], context: int = CONTEXT) -> list[int]:
    """The first sample of each CHUNK-long chunk that together hold every sample of spans (pairs (start, end) inside
    a sound of length samples) with context samples of the sound before and after it, but for the sound's own ends.
    A chunk starts context before the first wanted sample no earlier chunk gave its context, and no later than
    where it still ends inside the sound."""
    wanted = sorted((max(a, 0), min(b, length)) for a, b in spans if min(b, length) > max(a, 0))
    starts: list[int] = []
    served = 0  # every wanted sample before this has its chunk
    for a, b in wanted:
        at = max(a, served)
        while at < b:
            start = min(max(at - context, 0), max(length - CHUNK, 0))
            starts.append(start)
            served = at = length if start + CHUNK >= length else start + CHUNK - context
    return starts


class Network(Protocol):
    """The seam: one runtime's network on one device."""

    def run(self, spectra: bytes, chunks: int, frames: int) -> bytes:
        """The masks of chunks spectra (one after another, frames frames each), one chunk's after another's."""


class Unavailable(Exception):
    """An engine cannot be tried on this machine; its text says why."""


@dataclass(frozen=True)
class Engine:
    """A way to run the network: a runtime on a device. run_chunks knows engines only through this."""
    name: str
    facts: Callable[[], str]  # what it would run on, for the log; raises Unavailable where it cannot be tried
    start: Callable[[], Network]  # makes its network; an error here or in a run means the engine is not used


@dataclass(frozen=True)
class Card:
    """What the NVIDIA driver says of the machine's first GPU (native separator_gpu)."""
    driver: str | None  # the driver's version, where its management library says
    interface: int  # the CUDA driver interface's version: 13000 is 13.0
    name: str
    capability: tuple[int, int]  # compute capability
    free: int  # bytes of video memory
    total: int

    def __str__(self) -> str:
        return (f"{self.name}, compute capability {self.capability[0]}.{self.capability[1]}, "
                f"{self.free / 2 ** 20:.0f} of {self.total / 2 ** 20:.0f} MB of video memory free, NVIDIA driver "
                f"{self.driver or 'version not said'}, CUDA interface {self.interface // 1000}.{self.interface % 1000 // 10}")


def gpu_problem(card: Card) -> str | None:
    """Why the NVIDIA GPU is not given the CUDA kernels; None when it is."""
    if card.capability < SMALLEST_CAPABILITY:
        return (f"compute capability {card.capability[0]}.{card.capability[1]} is under "
                f"{SMALLEST_CAPABILITY[0]}.{SMALLEST_CAPABILITY[1]}, the first with the instructions the kernels use")
    if card.free < FREE_VIDEO_MEMORY:
        return f"under {FREE_VIDEO_MEMORY / 2 ** 20:.0f} MB of video memory is free"
    return None


@dataclass(frozen=True)
class Device:
    """What a Vulkan device says of itself (native separator_vulkan_devices, in its order), and its place there."""
    index: int
    name: str
    kind: str  # "discrete", "integrated", "virtual", "cpu" (a software renderer) or "other"
    vendor: int
    driver: str
    version: tuple[int, int, int]  # of Vulkan
    subgroup: tuple[int, int, int]  # the subgroup size, and the least and the most a pipeline may ask for instead
    matrices: tuple[tuple[int, int, int], ...]  # cooperative matrix shapes (M, N, K): 16-bit floats, 32-bit sums
    matrix2: bool  # VK_NV_cooperative_matrix2 with what the matrix2 attention uses
    float16: bool  # 16-bit float arithmetic and storage
    full_subgroups: bool
    buffer_limit: int  # bytes one buffer binding may hold
    shared_limit: int  # bytes of shared memory a shader may declare
    free: int  # bytes of device memory

    @classmethod
    def listed(cls, module) -> tuple[list[Device], dict[int, str]]:
        """(the devices the loader lists, in its order; by its place there, why a device's facts could not be
        read: one such device does not hide the others)."""
        devices, unread = [], {}
        for index, (said, why) in enumerate(module.separator_vulkan_devices()):
            if said is None:
                unread[index] = why
            else:
                name, kind, vendor, driver, version, subgroup, matrices, flags, sizes = said
                devices.append(cls(index, name, kind, vendor, driver, tuple(version), tuple(subgroup),
                                   tuple(map(tuple, matrices)), *flags, *sizes))
        return devices, unread

    def __str__(self) -> str:
        shapes = ", ".join("x".join(map(str, shape)) for shape in self.matrices) or "none"
        return (f"{self.name} ({VENDORS.get(self.vendor, 'maker')} {self.vendor:#06x}, {self.kind}), driver {self.driver}, "
                f"Vulkan {'.'.join(map(str, self.version))}, matrix shapes {shapes}"
                f"{' with matrix2' if self.matrix2 else ''}, 16-bit floats {'yes' if self.float16 else 'no'}, "
                f"subgroups of {self.subgroup[0]} ({self.subgroup[1]} to {self.subgroup[2]}), "
                f"{self.free / 2 ** 20:.0f} MB of device memory free, {self.buffer_limit / 2 ** 20:.0f} MB a buffer, "
                f"{self.shared_limit} bytes of shared memory")


@dataclass(frozen=True)
class ShaderSet:
    """The shaders one Vulkan engine runs the network with."""
    tier: str  # what its products run on: "matrix", "float16" or "float32" (a key of PRODUCTS)
    attention: str  # a key of ATTENTION
    matrix: tuple[int, int, int] | None  # the matrix shape the products are specialised to, on the matrix tier
    lanes: int  # the subgroup size its pipelines are made with
    fixed: bool  # its pipelines ask for that size: the device has a choice of sizes, and would give any of them

    def __str__(self) -> str:
        shape = f" {'x'.join(map(str, self.matrix))}" if self.matrix else ""
        return (f"{self.tier}{shape} products, {self.attention} attention, subgroups of {self.lanes}"
                f"{' asked for' if self.fixed else ''}")

    def kernels(self, folder: Path, limit: int | None = None) -> dict:
        """What native SeparatorVulkan takes; limit caps a buffer's bytes under the device's own (tests)."""
        (products, tile), (attention, queries) = PRODUCTS[self.tier], ATTENTION[self.attention]
        files = {"products": products, "attention": attention} | STEPS
        return {"files": {step: str(folder / name) for step, name in files.items()}, "matrix": self.matrix,
                "matrix2": self.attention == "matrix2", "tile": tile, "queries": queries, "subgroup": self.lanes,
                "required": self.fixed, "limit": limit}


def vulkan_problem(device: Device) -> str | None:
    """Why a Vulkan device is not tried at all; None when it is."""
    if device.kind not in DEVICE_KINDS:
        return "it is not a GPU" if device.kind == "cpu" else f"its kind is {device.kind}"
    if device.version < (1, 3):
        return "its driver is older than Vulkan 1.3, which the shaders are built for"
    if not device.full_subgroups:
        return "its driver cannot keep a shader's subgroups full, which every shader counts on"
    if device.free < FREE_VIDEO_MEMORY:
        return f"under {FREE_VIDEO_MEMORY / 2 ** 20:.0f} MB of its memory is free"
    return None


DEVICE_KINDS = ("discrete", "integrated", "virtual")  # the kinds of Vulkan device tried, in the order they are


def shader_sets(device: Device, shared: Callable[[str], int]) -> list[ShaderSet]:
    """The shader sets to try on a device, the fastest first; shared(file) is the shared memory a shader declares.

    A shader is picked only where it fits the device's shared memory. Attention runs on the matrix units where the
    device has them 16x16x16 in subgroups of 32 (matrix2 where it also has VK_NV_cooperative_matrix2), else plain.
    Products run, a tier a set:
    - matrix: on the matrix units, in the largest shape the device has that the shader takes;
    - float16: on 16-bit floats, where the device computes with them at full rate (an NVIDIA card without matrix
      units, GTX 10 and older, does so at 1/64 of it);
    - float32: on every device."""
    own, least, most = device.subgroup
    lanes = 32 if least <= 32 <= most else own
    # Where a device has a choice of sizes, its own is only one of them: the shaders are built as SPIR-V 1.6, for
    # which a driver may pick any size from the least to the most unless the pipeline asks for one.
    fixed = least < most
    fits = lambda name: shared(name) <= device.shared_limit  # noqa: E731
    cube = (16, 16, 16) in device.matrices and lanes == 32
    shapes = [shape for shape in device.matrices if shape[0] in (8, 16) and shape[1] in (8, 16) and shape[2] in (8, 16, 32)]
    sets = []
    if shapes and device.float16 and fits(PRODUCTS["matrix"][0]):
        attention = next((kind for kind, usable in (("matrix2", cube and device.matrix2), ("matrix", cube), ("plain", True))
                          if usable and fits(ATTENTION[kind][0])), None)
        if attention:
            sets.append(ShaderSet("matrix", attention, max(shapes, key=lambda each: (each[0] * each[1] * each[2], each)), lanes, fixed))
    if fits(ATTENTION["plain"][0]):  # the sets without matrix units, which also stand in where a driver's units fail
        if device.float16 and (device.matrices or device.vendor != NVIDIA) and fits(PRODUCTS["float16"][0]):
            sets.append(ShaderSet("float16", "plain", None, lanes, fixed))
        if fits(PRODUCTS["float32"][0]):
            sets.append(ShaderSet("float32", "plain", None, lanes, fixed))
    return sets




TICK = 0.05  # seconds between two looks of a thread that waits for chunks


class _Chunks:
    """The chunks waiting for an engine, the lowest first, each with its spectrum, made a few ahead on other threads
    so the STFT of the next chunks runs beside the network. An engine that fails, or whose held results are thrown
    away, hands its chunks back by their numbers."""

    AHEAD = 4

    def __init__(self, count: int, spectrum: Callable[[int], bytes]) -> None:
        self.spectrum = spectrum
        self.made = ordered_map(spectrum, range(count), self.AHEAD)  # each chunk's spectrum, as it is asked for
        self.making = threading.Lock()  # one engine at a time asks for the next
        self.next = 0
        self.returned: list[int] = []  # handed back, the lowest first
        self.left = count  # not yet finished
        self.changed = threading.Condition()
        self.failed: BaseException | None = None
        self.first: bytes | None = None  # the first chunk's spectrum, kept for the other engines' check

    def take(self, resting: threading.Event | None = None, unless: threading.Event | None = None,
             watch: Callable[[], None] = lambda: None) -> tuple[int, bytes] | None:
        """The next chunk and its spectrum; None once every chunk is finished, or once unless is set. It waits while
        resting is set and while no chunk is free, and calls watch() each time it looks again."""
        while True:
            with self.changed:
                if self.failed:
                    raise self.failed
                if not self.left or (unless is not None and unless.is_set()):
                    return None
                free = not (resting is not None and resting.is_set())
                again = self.returned.pop(0) if free and self.returned else None
            if again is not None:
                return again, self.spectrum(again)
            if free:
                with self.making:
                    made = next(self.made, None)
                    index, self.next = self.next, self.next + (made is not None)
                if made is not None:
                    if index == 0:
                        with self.changed:
                            self.first = made
                            self.changed.notify_all()
                    return index, made
            watch()
            with self.changed:  # every spectrum is out, or the chunks are another engine's for now
                self.changed.wait(TICK)

    def finished(self, chunks: int = 1) -> None:
        with self.changed:
            self.left -= chunks
            self.changed.notify_all()

    def give_back(self, indexes: Sequence[int]) -> None:
        with self.changed:
            self.returned = sorted({*self.returned, *indexes})
            self.changed.notify_all()

    def fail(self, error: BaseException) -> None:
        """Nothing waits on chunks that will not come."""
        with self.changed:
            self.failed = self.failed or error
            self.changed.notify_all()

    def first_spectrum(self) -> bytes:
        with self.changed:
            self.changed.wait_for(lambda: self.first is not None or self.failed is not None)
            if self.failed:
                raise self.failed
            return self.first


def _frames(spectrum: bytes) -> int:
    return len(spectrum) // (2050 * 2 * 4)  # a frame: 2050 rows of a (real, imaginary) pair of 32-bit floats


def run_chunks(count: int, spectrum: Callable[[int], bytes], finish: Callable[[int, bytes, bytes, bool], None],
               cpu: Engine, others: Sequence[Engine] = (), log: Callable[[str], None] = lambda line: None,
               agreement: Callable[[bytes, bytes], float] | None = None,
               settle: Callable[[bool], None] = lambda keep: None,
               abandon: Callable[[str], None] | None = None) -> str:
    """Run count chunks through the network: spectrum(i) gives chunk i's spectrum, finish(i, spectrum, masks, held)
    takes its masks (from either engine's thread, in any order). cpu is the engine that always works; others are
    tried in order, and the first that passes takes over. Returns how the chunks were run, for the log.

    The CPU starts on the first chunk at once. Another engine runs that chunk too, for the check (module docstring,
    step 2), and does not wait for the CPU's answer: it runs ahead through the chunks after it, and finish gets each
    of those with held true, a result to keep only if the check passes. When the CPU's first chunk is in, the check
    is made. Passed: settle(True) says the held results count, and the engine has the chunks from there, one a run,
    while the CPU rests (two a run took a GPU twice as long: no gain). Not passed: settle(False) says to throw them
    away, their chunks go back to be run again, and the engine's network is let go before the next engine starts.
    An error after an engine took over hands back the chunk it held, and the CPU carries on.

    A driver that stops answering is an outside defect: its thread cannot be stopped, and this function cannot
    return while it lives. So whenever the CPU's thread has nothing to run, it asks whether the engine now tried or
    used can still finish sooner than the CPU would have (_overdue). Once it cannot, abandon(why) is called, which
    must end the process; where there is none, or it comes back, SeparationError is raised if chunks are left."""
    agreement = agreement or native.module.separator_agreement
    chunks = _Chunks(count, spectrum)
    resting = threading.Event()  # set while another engine has the chunks
    first: dict = {}  # the CPU's masks for chunk 0 and the seconds they took
    first_done = threading.Event()
    settled = threading.Event()  # set once the other engines are decided: one has the chunks, or none will
    ended = threading.Event()  # set once the other engines' thread has come back
    done = {"CPU": 0, "other": 0}
    how = [cpu.name]
    trying = {"name": "", "since": time.monotonic(), "each": 0.0}  # the other engine now tried: what the stall rule reads

    def watch() -> None:
        if not ended.is_set() and "seconds" in first:
            why = _overdue(trying["name"], time.monotonic() - trying["since"], chunks.left, first["seconds"], trying["each"])
            if why is not None:
                if abandon is not None:
                    abandon(why)
                if chunks.left:  # with every chunk finished there is nothing to fail: the engine is waited for
                    raise SeparationError(why)

    def on_cpu() -> None:
        try:
            log(f"{cpu.name}: {cpu.facts()}")
            network = cpu.start()
            while taken := chunks.take(resting, watch=watch):
                index, made = taken
                started = time.monotonic()
                masks = network.run(made, 1, _frames(made))
                seconds = time.monotonic() - started
                if index == 0:
                    first.update(masks=masks, seconds=seconds)
                    first_done.set()
                finish(index, made, masks, False)
                chunks.finished()
                done["CPU"] += 1
                log(f"chunk {index + 1} of {count} on the CPU in {seconds:.3f} s")
                if index == 0 and others:
                    # Which engine runs the second chunk must not hang on which thread gets there first: two
                    # installs on one machine would write different voices. An engine that is ready decides in a
                    # moment; one that takes longer than a CPU chunk to get ready is not waited for.
                    settled.wait(seconds)
            while not ended.wait(TICK):  # every chunk is finished: a healthy engine sees that and comes back
                watch()
        except BaseException as error:
            chunks.fail(error)
            raise
        finally:
            first_done.set()

    def on_others() -> None:
        try:
            for engine in others:
                if try_engine(engine):
                    return
        finally:
            settled.set()
            ended.set()

    def try_engine(engine: Engine) -> bool:
        """Try one engine: True when no other is to be tried after it. Its network lives only as long as this call,
        so a rejected engine's device memory is free before the next engine starts."""
        held: list[int] = []  # the chunks it ran ahead of the check, their results held by finish
        current, used = None, False

        def ran(network: Network, taken: tuple[int, bytes], keep: bool) -> None:
            index, made = taken
            trying["since"] = started = time.monotonic()
            masks = network.run(made, 1, _frames(made))
            seconds = time.monotonic() - started
            finish(index, made, masks, not keep)
            device = getattr(network, "device_seconds", None)  # a GPU network says how long the device had it
            log(f"chunk {index + 1} of {count} on the {engine.name} in {seconds:.3f} s"
                + ("" if device is None else f" ({device:.3f} s on the device)") + ("" if keep else ", held for the check"))

        try:
            trying.update(name=engine.name, since=time.monotonic(), each=0.0)
            log(f"{engine.name}: {engine.facts()}")
            started = time.monotonic()
            network = engine.start()
            made = chunks.first_spectrum()
            trying["since"] = time.monotonic()
            network.run(made, 1, _frames(made))  # the first run also warms the device up
            ready = time.monotonic() - started
            trying["since"] = started = time.monotonic()
            masks = network.run(made, 1, _frames(made))
            trying["each"] = each = time.monotonic() - started
            while not first_done.is_set() and (current := chunks.take(unless=first_done)):
                ran(network, current, False)
                held.append(current[0])
                current = None
            first_done.wait()
            if "masks" not in first:
                return True  # the CPU failed, and its error is the run's
            apart = agreement(masks, first["masks"])
            verdict = (f"ready in {ready:.1f} s, {each:.3f} s a chunk against the CPU's {first['seconds']:.3f} s, "
                       f"its masks {apart:.1f} dB from the CPU's (limit {AGREE_DB:.0f} dB)")
            if not (apart <= AGREE_DB and each < first["seconds"]):
                log(f"{engine.name} not used: {verdict}" + (f"; the {len(held)} chunks it ran ahead are run again" if held else ""))
                return False
            log(f"{engine.name} used from here: {verdict}" + (f"; the {len(held)} chunks it ran ahead count" if held else ""))
            how[0] = engine.name
            used = True
            settle(True)
            chunks.finished(len(held))
            done["other"] += len(held)
            resting.set()
            settled.set()
            while current := chunks.take():
                ran(network, current, True)
                chunks.finished()
                done["other"] += 1
                current = None
            return True
        except Unavailable as why:
            log(f"{engine.name} not tried: {why}")
            return False
        except Exception as error:  # a device's errors come as its runtime's: any of them ends this engine
            log(f"{engine.name} failed: {' '.join(str(error).split()) or type(error).__name__}")
            if used:  # it had the chunks: the CPU takes them back, and no other engine is tried
                how[0] = f"{engine.name}, then the CPU after it failed"
                if current:
                    chunks.give_back([current[0]])
                resting.clear()
            return used
        finally:
            if not used and (held or current):
                settle(False)
                chunks.give_back([*held, *([current[0]] if current else [])])

    try:  # the two side by side; the CPU's error, if any, is the run's
        if not others:
            ended.set()
        for _ in ordered_map(lambda work: work(), [on_cpu, on_others] if others else [on_cpu], 2):
            pass
    finally:
        chunks.made.close()
    return f"{how[0]} ({done['CPU']} chunks on the CPU, {done['other']} on another engine)"


def _overdue(name: str, waited: float, left: int, cpu: float, each: float) -> str | None:
    """Why an engine that has shown nothing for waited seconds is given up, with left chunks unfinished; None while
    it is not. cpu and each are the seconds a chunk takes the CPU and the engine (0 until the engine was timed).

    The engine is given up once it can no longer finish sooner than the CPU would have, had the CPU taken the
    chunks when the engine last showed something: the CPU would need left * cpu from then, and the engine, were it
    to come back this moment, (left - 1) * each more. With nothing left it gets one CPU chunk's time to come back.
    No fixed time is in it: a slow machine waits longer, a short job less."""
    allowed = max(left, 1) * cpu - max(left - 1, 0) * each
    if waited < allowed:
        return None
    if not left:
        return f"{name} given up: every chunk is finished, and it has not come back for {waited:.1f} s"
    return (f"{name} given up: nothing from it for {waited:.1f} s, and the CPU alone would have run the {left} "
            f"chunks left in {left * cpu:.1f} s")


# The separating process


def _log(line: str) -> None:
    print(LOG + line, file=sys.stderr, flush=True)  # a step's worker sends what it and its children print to the log


def cpu_engine(module, folder: Path, threads: int) -> Engine:
    """ONNX Runtime on the CPU: the engine every machine has."""
    def start() -> Network:
        # On Windows the native module loads the library so that the Visual C++ runtime beside it is the one it
        # gets (separator.rs load_beside): its absolute path is what makes that folder the first looked in.
        return module.SeparatorNetwork(str(runtime(folder).absolute()), str(folder / GRAPH), threads)

    return Engine("CPU", lambda: f"ONNX Runtime, {threads} threads", start)


def _stored(index: dict) -> tuple[list, list]:
    """separator.json as the native networks take it: its tensors and its bands."""
    tensors = [(name, entry["kind"], entry["shape"], entry["at"], entry.get("scales", 0))
               for name, entry in index["tensors"].items()]
    return tensors, [tuple(band) for band in index["bands"]]


def nvidia_engine(module, folder: Path, index: dict) -> Engine:
    """Our CUDA kernels on the machine's NVIDIA GPU, through its driver: an engine no build has unless it is asked
    for (native/src/cuda.rs). It is tried where the native module was built with it, the model folder holds the
    kernels (KERNELS) as pinned, and the GPU passes gpu_problem."""
    def facts() -> str:
        if not hasattr(module, "SeparatorCuda"):
            raise Unavailable("this build has no CUDA engine (native/src/cuda.rs says how to build one in)")
        if problem := pinned_problem(folder, KERNELS):
            raise Unavailable(f"the model folder does not hold the CUDA kernels: {problem}")
        try:
            card = Card(*module.separator_gpu())
        except Exception as error:
            raise Unavailable(" ".join(str(error).split())) from None
        if problem := gpu_problem(card):
            raise Unavailable(f"{problem} ({card})")
        return str(card)

    def start() -> Network:
        network = module.SeparatorCuda(str(folder / WEIGHTS), *_stored(index), [str(folder / name) for name in KERNELS], FRAMES)
        _log(f"NVIDIA GPU (CUDA): the driver took the kernels {network.kernels}")
        return network

    return Engine("NVIDIA GPU (CUDA)", facts, start)


def vulkan_network(module, folder: Path, index: dict, device: Device, shaders: ShaderSet, frames: int = FRAMES,
                   limit: int | None = None) -> Network:
    """The network on a Vulkan device with a shader set, for chunks of frames frames."""
    return module.SeparatorVulkan(device.index, str(folder / WEIGHTS), *_stored(index), frames, shaders.kernels(folder, limit))


def vulkan_engines(module, folder: Path, index: dict, log: Callable[[str], None]) -> list[Engine]:
    """Our Vulkan shaders on each GPU the system's Vulkan loader lists: an engine for each shader set of each device
    (shader_sets), discrete GPUs before integrated ones. Says in the log what was listed and what is left out."""
    if problem := pinned_problem(folder, SHADERS):
        log(f"Vulkan GPUs not tried: the model folder does not hold the Vulkan shaders: {problem}")
        return []
    try:
        devices, unread = Device.listed(module)
    except Exception as error:  # no Vulkan loader, or one that fails
        log(f"Vulkan GPUs not tried: {' '.join(str(error).split())}")
        return []
    for place, why in unread.items():
        log(f"Vulkan device {place} not tried: what it can do could not be read: {' '.join(why.split())}")
    engines = []
    for device in sorted(devices, key=lambda each: DEVICE_KINDS.index(each.kind) if each.kind in DEVICE_KINDS else len(DEVICE_KINDS)):
        problem = vulkan_problem(device)
        sets = [] if problem else shader_sets(device, lambda name: module.separator_shared_memory(str(folder / name)))
        if not sets:
            log(f"Vulkan device {device.index} not tried: {problem or 'no shader fits its shared memory'} ({device})")
        for shaders in sets:
            def start(device=device, shaders=shaders) -> Network:
                network = vulkan_network(module, folder, index, device, shaders)
                log(f"Vulkan device {device.index}: the network lies in {network.buffers} buffers")
                return network

            engines.append(Engine(f"Vulkan GPU {device.index} ({shaders.tier})", lambda device=device, shaders=shaders: f"{shaders}; {device}", start))
    return engines


def gpu_engines(module, folder: Path, index: dict, log: Callable[[str], None]) -> list[Engine]:
    """The engines tried beside the CPU, in order: the one place that decides it, the same on every system and
    every card. Each GPU the system lists gets the Vulkan shaders, a device and a shader set at a time. An engine
    that cannot be tried says why when its turn comes (Engine.facts), and each must pass run_chunks' check against
    the CPU, the only guard there is.

    ONLY_ENGINE in the environment is a developer's switch: it keeps only the engines whose name holds its value,
    for measuring one of them, and "cuda" asks for the CUDA engine instead, which nothing else tries."""
    only = os.environ.get(ONLY_ENGINE)
    if only and only.lower() == "cuda":
        log(f"{ONLY_ENGINE}={only}: the CUDA engine alone is tried")
        return [nvidia_engine(module, folder, index)]
    engines = vulkan_engines(module, folder, index, log)
    if only:
        log(f"{ONLY_ENGINE}={only}: of the GPU engines {[engine.name for engine in engines]}, only those so named are tried")
        engines = [engine for engine in engines if only in engine.name]
    return engines


ONLY_ENGINE = "WOZMOD_DREAM_ENGINE"  # a developer's switch, to measure one GPU engine: a part of its name, or cuda


def worker(job_path: str) -> None:
    """The separating process's main: run the job the file names (separate() writes it), each chunk's voice into the
    job's folder as it is finished, and a line with a chunk's number on stdout for each. A chunk whose voice is there
    already is not run again."""
    try:
        job = json.loads(Path(job_path).read_text(encoding="utf-8"))
        module, folder, out = native.module, Path(job["model"]), Path(job["out"])
        index = json.loads((folder / INDEX).read_text(encoding="utf-8"))
        bands = [tuple(band) for band in index["bands"]]
        todo = [k for k in range(len(job["chunks"])) if not (out / f"{k}.f32").is_file()]

        def spectrum(i: int) -> bytes:
            name, start = job["chunks"][todo[i]]
            with open(job["sounds"][name], "rb") as stream:
                stream.seek(8 * start)
                sound = stream.read(8 * CHUNK)
            return module.separator_spectrum(sound + bytes(8 * CHUNK - len(sound)))  # silence past the scene's end

        finishing: queue.SimpleQueue = queue.SimpleQueue()
        drained = threading.Event()  # set once the finisher has written all it was given

        def finish(i: int, made: bytes, masks: bytes, held: bool) -> None:
            finishing.put((todo[i], made, masks, held))

        def finisher() -> None:  # the voice of each chunk, beside the network's next run
            held = []  # written as .held: the chunks an engine ran ahead of its check
            try:
                while (item := finishing.get()) is not None:
                    if isinstance(item, bool):  # the check is made: what was held is kept, or thrown away
                        for k in held:
                            if item:
                                os.replace(out / f"{k}.held", out / f"{k}.f32")
                                print(k, flush=True)
                            else:
                                (out / f"{k}.held").unlink()
                        held.clear()
                        continue
                    k, made, masks, wait = item
                    voice = module.separator_voice(made, masks, bands, CHUNK)
                    (out / f"{k}.part").write_bytes(voice)
                    os.replace(out / f"{k}.part", out / (f"{k}.held" if wait else f"{k}.f32"))
                    if wait:
                        held.append(k)
                    else:
                        print(k, flush=True)
            finally:
                drained.set()

        def abandon(why: str) -> None:
            # A driver that stops answering cannot be interrupted or repaired from here; ending the process is the
            # one way past it. What is finished stays, and separate() runs what is left on the CPU.
            _log(why)
            finishing.put(None)
            drained.wait()
            left = sum(not (out / f"{k}.f32").is_file() for k in todo)
            _log(f"this process ends here, with {left} chunks left for the CPU" if left else "this process ends here: nothing is left")
            sys.stderr.flush()
            os._exit(STALLED if left else 0)

        def runner() -> str:
            try:
                return run_chunks(len(todo), spectrum, finish, cpu_engine(module, folder, job["threads"]), others, _log,
                                  settle=finishing.put, abandon=abandon)
            finally:
                finishing.put(None)

        started = time.monotonic()
        _log(f"{len(todo)} of {len(job['chunks'])} chunks of {CHUNK / RATE:.0f} s to separate")
        others = gpu_engines(module, folder, index, _log) if job["gpu"] else []
        if not job["gpu"]:
            _log(f"no GPU tried: {job['why']}")
        how, _ = ordered_map(lambda work: work(), [runner, finisher], 2) if todo else ("nothing left to run", None)
        seconds = time.monotonic() - started
        _log(f"path: {how}; {len(todo)} chunks in {seconds:.1f} s ({seconds / max(len(todo), 1):.2f} s a chunk)")
    except BaseException:
        import traceback

        traceback.print_exc()
        sys.stderr.flush()
        os._exit(FAILED)  # not sys.exit: a device's threads may still run, and they must not hold the process


FAILED = 3  # the separating process's exit code for an error it caught
STALLED = 4  # and for a GPU engine it gave up waiting for: like a crash, the rest runs on the CPU


def _command(job: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER, str(job)]
    return [sys.executable, "-c", _BOOT, *(os.path.abspath(p) for p in sys.path), str(job)]


def separate(sounds: dict[str, bytes], spans: dict[str, Sequence[tuple[int, int]]], model: Path, jobs: int,
             progress: Callable[[int, int], None] = lambda done, total: None, gpu: bool = True) -> dict[str, bytes]:
    """The voice in each sound (interleaved stereo 32-bit floats at RATE, by name) over its spans (start, end in
    samples a channel): a sound as long, the voice where a chunk of cover() ran and silence elsewhere. model is the
    model folder (MODEL); jobs caps the CPU threads; progress(done, total) hears of each finished chunk and may
    raise to stop (the process is then killed); gpu False keeps the work on the CPU.

    The work runs in a process of its own. If that ends without finishing (a graphics driver crashed it, or stopped
    answering and the process gave it up), a second one finishes the chunks that are left on the CPU alone; SeparationError when that fails too, or when the first
    failed with an error of its own."""
    lengths = {name: len(sound) // 8 for name, sound in sounds.items()}
    plan = [(name, start) for name in sorted(sounds) for start in cover(lengths[name], spans[name])]
    threads = max(1, min(native.module.separator_cores(), jobs))
    with tempfile.TemporaryDirectory(prefix="wozmod-separate-", ignore_cleanup_errors=True) as scratch:
        folder = Path(scratch)
        for name, sound in sounds.items():
            (folder / f"{name}.sound").write_bytes(sound)
        (folder / "out").mkdir()
        job = {"model": str(Path(model).absolute()), "out": str(folder / "out"), "threads": threads, "chunks": plan,
               "sounds": {name: str(folder / f"{name}.sound") for name in sounds}}
        finished = 0
        tries = [(True, "")] if gpu else []
        tries.append((False, "the process that tried it ended without finishing" if gpu else "the caller asked for the CPU"))
        for gpu, why in tries:
            (folder / "job.json").write_text(json.dumps(job | {"gpu": gpu, "why": why}), encoding="utf-8")
            process = platform.ProcessTree(_command(folder / "job.json"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
            try:
                for _ in process.stdout:  # a line a finished chunk
                    finished += 1
                    progress(finished, len(plan))
                code = process.wait()
            except BaseException:
                process.kill_tree()
                process.wait()
                raise
            finally:
                process.stdout.close()
                process.close()
            if code == 0:
                break
            if code == FAILED or not gpu:
                raise SeparationError(f"the Dream voices could not be separated (the separating process ended with "
                                      f"code {code}; the install log says why)")
            _log(f"the separating process ended without finishing (code {code}, {code & 0xFFFFFFFF:#x}); the rest runs "
                 f"on the CPU. The last engine named above is the one it ended in")
        voices = {}
        for name in sounds:
            pieces = [(start, (folder / "out" / f"{k}.f32").read_bytes()[:8 * (lengths[name] - start)])
                      for k, (scene, start) in enumerate(plan) if scene == name]
            voices[name] = native.module.separator_joined(lengths[name], pieces)
        return voices
