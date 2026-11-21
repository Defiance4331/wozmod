"""Compile the Dream voice separator's GPU kernels: the Vulkan shaders (packaging/separator/vulkan/) to SPIR-V, which
any Vulkan driver takes, and, when asked, the CUDA ones (packaging/separator/kernels.cu) to PTX, the text the NVIDIA
driver compiles for a player's card.

    python packaging/separator_kernels.py [--cuda] [FOLDER]

FOLDER is the separator folder (default: the main checkout's work/release-assets/dream-voice/). It gets one SPIR-V
file per row of SHADERS, made by the pinned glslang ("separator" "shaders" in packaging/pins.json), unpacked into
FOLDER/glslang/.

--cuda also makes one PTX file per build that packaging/pins.json names ("separator" "kernels"), each with the
pinned NVRTC and the pinned CUDA headers, which are unpacked into FOLDER/nvrtc/:
- separator-cuda13.ptx (NVRTC 13, PTX version 9.5): the drivers of the CUDA 13 branch load it. The faster build.
- separator-cuda12.ptx (NVRTC 12.0, PTX version 8.0): drivers from R525 (late 2022) load it; 6 % slower.
Both are for compute capability 7.5 (the tensor-core instructions the kernels use) and later. No release carries
them: the CUDA engine is out of every build, and native/src/cuda.rs says how to turn it back on.

glslang, NVRTC and the headers are build tools: a release carries the SPIR-V, and nothing of a Vulkan SDK or the
CUDA toolkit. Linux only (the outputs are the same bytes for Windows). The outputs are pinned in
voices/separation.py (SHADERS, KERNELS), so a build that differs is noticed.
"""

from __future__ import annotations

import ctypes
import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pins  # noqa: E402
from separator_runtime import wheel  # noqa: E402

SOURCE = Path(__file__).resolve().parent / "separator/kernels.cu"
OPTIONS = ["--gpu-architecture=compute_75", "--std=c++17", "-default-device"]
VULKAN = Path(__file__).resolve().parent / "separator/vulkan"
# Each SPIR-V file: its source in VULKAN and the names the source is compiled with (voices/separation.py picks among
# them by what the player's device can do).
SHADERS = {
    "separator-products-matrix.spv": ("gemm_cm.comp", ()),
    "separator-products-float16.spv": ("gemm_plain.comp", ("F16MATH=1",)),
    "separator-products-float32.spv": ("gemm_plain.comp", ("F16MATH=0",)),
    "separator-attention-matrix2.spv": ("attn_cm2.comp", ()),
    "separator-attention-matrix.spv": ("attn_cm.comp", ()),
    "separator-attention-plain.spv": ("attn_plain.comp", ()),
    "separator-norm.spv": ("small.comp", ("K_RMSNORM",)),
    "separator-gates.spv": ("small.comp", ("K_GATES",)),
    "separator-bandsplit.spv": ("small.comp", ("K_BANDSPLIT",)),
    "separator-glu.spv": ("small.comp", ("K_GLU",)),
}


def compiled(library: Path, include: Path) -> bytes:
    """SOURCE as PTX, by the NVRTC at library with the headers in include."""
    nvrtc, program = ctypes.CDLL(str(library)), ctypes.c_void_p()

    def checked(code: int, what: str) -> None:
        if code:
            size = ctypes.c_size_t()
            nvrtc.nvrtcGetProgramLogSize(program, ctypes.byref(size))
            log = ctypes.create_string_buffer(size.value or 1)
            nvrtc.nvrtcGetProgramLog(program, log)
            raise SystemExit(f"{library.name}: {what} failed ({code})\n{log.value.decode(errors='replace')}")

    checked(nvrtc.nvrtcCreateProgram(ctypes.byref(program), SOURCE.read_bytes(), b"kernels.cu", 0, None, None), "create")
    options = [option.encode() for option in (*OPTIONS, f"-I{include}")]
    checked(nvrtc.nvrtcCompileProgram(program, len(options), (ctypes.c_char_p * len(options))(*options)), "compile")
    size = ctypes.c_size_t()
    checked(nvrtc.nvrtcGetPTXSize(program, ctypes.byref(size)), "PTX size")
    ptx = ctypes.create_string_buffer(size.value)
    checked(nvrtc.nvrtcGetPTX(program, ptx), "PTX")
    return ptx.value  # the text, without the 0 that ends it


def shaders(folder: Path) -> dict[str, tuple[int, str]]:
    """Write each of SHADERS into folder, compiled by the pinned glslang; return each file's (size, SHA-256)."""
    pin = pins.pins()["separator"]["shaders"]
    tools = folder / "glslang" / pin["compiler"].removesuffix(".tar.gz")
    if not (tools / pin["program"]).is_file():
        with tarfile.open(wheel(folder, pin["compiler"])) as archive:
            archive.extractall(tools, filter="data")
    made = {}
    for name, (source, names) in SHADERS.items():
        command = [tools / pin["program"], *pin["options"], *(f"-D{each}" for each in names), "-o", folder / name, VULKAN / source]
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if done.returncode:
            raise SystemExit(f"{source} ({' '.join(names)}) did not compile\n{done.stdout}{done.stderr}")
        data = (folder / name).read_bytes()
        made[name] = (len(data), hashlib.sha256(data).hexdigest())
    return made


def build(folder: Path) -> dict[str, tuple[int, str]]:
    """Write each pinned build's PTX into folder; return each file's (size, SHA-256)."""
    made = {}
    for each in pins.pins()["separator"]["kernels"]:
        tools = folder / "nvrtc" / each["ptx"].removesuffix(".ptx")
        for name in (each["compiler"], each["headers"]):
            with zipfile.ZipFile(wheel(folder, name)) as archive:
                archive.extractall(tools, [member for member in archive.namelist() if member.startswith("nvidia/")])
        ptx = compiled(tools / each["library"], tools / each["include"])
        (folder / each["ptx"]).write_bytes(ptx)
        made[each["ptx"]] = (len(ptx), hashlib.sha256(ptx).hexdigest())
    return made


if __name__ == "__main__":
    arguments = [each for each in sys.argv[1:] if each != "--cuda"]
    into = Path(arguments[0]) if arguments else pins.separator_folder()
    for name, (size, digest) in (shaders(into) | (build(into) if "--cuda" in sys.argv else {})).items():
        print(f'    "{name}": ({size:_}, "{digest}"),')
