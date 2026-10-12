# PyInstaller spec for the `wozmod` and `wozmod-setup` programs: python packaging/build.py runs it.
# One folder, not one file: a one-file program would unpack the 140 MB ffmpeg (copied in
# afterwards by packaging/build.py, so PyInstaller does not scan it) on every start.
# Both programs share one _internal/ folder.
import sys
from pathlib import Path

root = Path(SPECPATH).parent
# The python.org Windows build keeps the Tcl 9 and Tk 9 scripts in two zip files beside Python, not inside
# the DLLs, so PyInstaller leaves them out. Tcl finds them when they sit next to tcl90.dll.
tcl_zips = [(str(zip_file), ".") for zip_file in (Path(sys.base_prefix) / "tcl").glob("libt*.zip")] if sys.platform == "win32" else []
common = dict(
    pathex=[str(root / "src")],
    datas=[
        (str(root / "data/art"), "data/art"),
        (str(root / "data/text"), "data/text"),
        (str(root / "runtime"), "runtime"),
        *tcl_zips,
    ],
    hiddenimports=["wozmod_native"],  # the Rust codecs; native.py imports them in a try block
)

command = Analysis([str(root / "src/wozmod/__main__.py")], **common)
window = Analysis([str(root / "src/wozmod/window.py")], **common)
MERGE((command, "wozmod", "wozmod"), (window, "wozmod-setup", "wozmod-setup"))

# UTF-8 mode: the program reads UTF-8 files without naming the encoding, and Windows would use cp1252.
utf8 = [("X utf8", None, "OPTION")]
command_exe = EXE(PYZ(command.pure), command.scripts, utf8, exclude_binaries=True, name="wozmod", console=True)
window_exe = EXE(PYZ(window.pure), window.scripts, utf8, exclude_binaries=True, name="wozmod-setup", console=False)
COLLECT(command_exe, command.binaries, command.datas, window_exe, window.binaries, window.datas, name="wozmod")
