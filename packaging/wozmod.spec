# PyInstaller spec for the `wozmod` program: python packaging/build.py runs it.
# One folder, not one file: a one-file program would unpack the 140 MB ffmpeg (copied in
# afterwards by packaging/build.py, so PyInstaller does not scan it) on every start.
from pathlib import Path

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "src/wozmod/__main__.py")],
    pathex=[str(root / "src")],
    datas=[
        (str(root / "data/art"), "data/art"),
        (str(root / "data/text"), "data/text"),
        (str(root / "runtime"), "runtime"),
    ],
    hiddenimports=["wozmod_native"],  # the Rust codecs; native.py imports them in a try block
    excludes=["tkinter"],
)
exe = EXE(PYZ(a.pure), a.scripts, exclude_binaries=True, name="wozmod", console=True)
COLLECT(exe, a.binaries, a.datas, name="wozmod")
