# PyInstaller spec for the `wozmod` program: python packaging/build.py runs it.
# One folder, not one file: a one-file program would unpack itself on every start, and in every build worker.
import sys
from pathlib import Path

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "src"))
from wozmod.resources import PACKAGED
# The python.org Windows build keeps the Tcl 9 and Tk 9 scripts in two zip files beside Python, not inside
# the DLLs, so PyInstaller leaves them out. Tcl finds them when they sit next to tcl90.dll.
tcl_zips = [(str(zip_file), ".") for zip_file in (Path(sys.base_prefix) / "tcl").glob("libt*.zip")] if sys.platform == "win32" else []
# What the install never loads (a full install with the built program proved it; repeat one when this list changes):
# - numpy and cairo: only the lettering render (wozmod.fonts, run on the developer machine) imports them;
# - Pillow's optional native modules, each imported in a try block that Pillow falls back from: AVIF, WebP, fonts,
#   colour management, Tk images, ImageMath and ImageMorph. The install reads and writes PNG with Pillow's core;
# - ssl and its OpenSSL library: an install makes no connection (tests/test_no_downloads.py). hashlib keeps libcrypto.
EXCLUDES = ["numpy", "cairo", "fontforge", "PIL._avif", "PIL._webp", "PIL._imagingft", "PIL._imagingcms",
            "PIL._imagingtk", "PIL._imagingmath", "PIL._imagingmorph", "ssl", "_ssl"]
# One program, as the console program wozmod was: wozmod-setup, which packaging/build.py adds beside it (a small
# launcher on Windows, a script on Linux), starts it with --setup-window (wozmod.__main__.program).
program = Analysis(
    [str(root / "src/wozmod/__main__.py")],
    pathex=[str(root / "src")],
    datas=[
        *((str(root / name), name if (root / name).is_dir() else str(Path(name).parent)) for name in PACKAGED),
        *tcl_zips,
    ],
    hiddenimports=["wozmod_native", "wozmod.window"],  # the Rust codecs (native.py imports them in a try block)
    excludes=EXCLUDES,
)
# The bootloader starts Python isolated, so it ignores PYTHONUTF8 and PYTHONHASHSEED, which install/steps.FIXED sets
# for each step worker: pin them here. UTF-8 mode: Windows would read files with cp1252. Hash seed 0: a step's set
# order, and so what it writes, is the same in every worker. PyInstaller 6.22 on Python 3.14 happens to give seed 0
# without it (it always sets hash_seed, which turns use_hash_seed on); the option makes it ours, not a side effect.
options = [("X utf8", None, "OPTION"), ("hash_seed=0", None, "OPTION")]
exe = EXE(PYZ(program.pure), program.scripts, options, exclude_binaries=True, name="wozmod", console=True)
COLLECT(exe, program.binaries, program.datas, name="wozmod")
