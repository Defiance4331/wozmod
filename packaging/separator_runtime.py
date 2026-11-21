"""Fetch the Dream voice separator's runtime (ONNX Runtime and, on Windows, the Visual C++ runtime it needs) for both
systems.

    python packaging/separator_runtime.py [FOLDER]

FOLDER is the separator folder (default: the main checkout's work/release-assets/dream-voice/, where
packaging/separator_model.py writes the model). It gets linux/ and windows/ with each system's libraries, and
licenses/linux/ and licenses/windows/ with the notices the same wheels carry. Every wheel and every file taken from
one is pinned in packaging/pins.json ("separator"): a wheel is downloaded from PyPI only when FOLDER/wheels/ does not
hold it, and a file that is not the pinned one stops the run. Releases and installs download nothing: this is the
one step that does (with packaging/separator_kernels.py, for its compiler), run when a pin changes.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pins  # noqa: E402
import windows_imports  # noqa: E402


def wheel(folder: Path, name: str) -> Path:
    """The pinned wheel called name in folder/wheels/, downloaded when it is not there."""
    pin = pins.pins()["separator"]["wheels"][name]
    path = folder / "wheels" / name
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        with urllib.request.urlopen(pin["url"]) as response, part.open("wb") as out:
            while block := response.read(1 << 20):
                out.write(block)
        part.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]:
        raise SystemExit(f"{path} is not the wheel {pins.PINS} pins; delete it to download it again")
    return path


def fetch(folder: Path) -> None:
    for system in ("linux", "windows"):
        for kind, into in (("files", folder / system), ("licenses", folder / "licenses" / system)):
            into.mkdir(parents=True, exist_ok=True)
            for pin in pins.pins()["separator"][kind][system]:
                with zipfile.ZipFile(wheel(folder, pin["wheel"])) as archive:
                    data = archive.read(pin["member"])
                if len(data) != pin["size"] or hashlib.sha256(data).hexdigest() != pin["sha256"]:
                    raise SystemExit(f"{pin['member']} in {pin['wheel']} is not the file {pins.PINS} pins")
                (into / pin["file"]).write_bytes(data)
                if system == "windows" and kind == "files":  # the Visual C++ runtime the library imports, beside it
                    windows_imports.bundle(into / pin["file"], wheel(folder, pins.pins()["separator"]["imports"]["windows"]))
        if problem := pins.separator_problem(system, folder):
            raise SystemExit(problem)
        print(f"{folder / system}: {len(pins.pins()['separator']['files'][system])} files as pinned")


if __name__ == "__main__":
    fetch(Path(sys.argv[1]) if len(sys.argv) > 1 else pins.separator_folder())
